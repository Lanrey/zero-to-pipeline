from __future__ import annotations

import configparser
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import structlog

from data_pipeline.config import settings

logger = structlog.get_logger(__name__)

_AWS_PROFILE_DEFAULT = "Lanrey"
_AWS_REGION_DEFAULT = "us-east-1"  # Lanrey account's Bedrock region
_MODEL_DEFAULT = "us.anthropic.claude-sonnet-4-6"


def _load_aws_profile_credentials(profile: str) -> tuple[str, str, str | None]:
    """Load AWS credentials from ~/.aws/credentials for a named profile.

    Returns (access_key_id, secret_access_key, session_token).
    """
    creds_path = os.path.expanduser("~/.aws/credentials")
    config = configparser.ConfigParser()
    config.read(creds_path)
    if profile not in config:
        raise RuntimeError(f"AWS profile '{profile}' not found in {creds_path}")
    section = config[profile]
    access_key = section.get("aws_access_key_id", "")
    secret_key = section.get("aws_secret_access_key", "")
    session_token = section.get("aws_session_token") or None
    if not access_key or not secret_key:
        raise RuntimeError(f"AWS profile '{profile}' is missing access key or secret key")
    return access_key, secret_key, session_token


@dataclass(slots=True)
class LLMResult:
    """Result from an LLM completion call."""

    text: str
    parsed: dict[str, Any] | None = None
    success: bool = True
    error: str | None = None


class LLMProvider(ABC):
    """Base class for LLM providers."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def complete(self, prompt: str, *, timeout: int = 30) -> LLMResult: ...

    def chat(self, messages: list[dict], *, system: str, max_tokens: int = 2048, timeout: int = 60) -> LLMResult:
        """Multi-turn chat using full message history. Default impl wraps complete()."""
        last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return self.complete(last, timeout=timeout)


class BedrockProvider(LLMProvider):
    """AWS Bedrock provider — Claude via the Lanrey AWS profile.

    Credential resolution order:
    1. AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY env vars (explicit)
    2. AWS_PROFILE env var → ~/.aws/credentials
    3. Lanrey profile in ~/.aws/credentials (hardcoded default for this demo)
    """

    @property
    def name(self) -> str:
        return "bedrock"

    def is_available(self) -> bool:
        if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
            return True
        profile = os.environ.get("AWS_PROFILE", _AWS_PROFILE_DEFAULT)
        try:
            _load_aws_profile_credentials(profile)
            return True
        except RuntimeError:
            return False

    def chat(self, messages: list[dict], *, system: str, max_tokens: int = 2048, timeout: int = 60) -> LLMResult:
        """Multi-turn chat with full conversation history."""
        return self._invoke(messages, system=system, max_tokens=max_tokens, timeout=timeout)

    def complete(self, prompt: str, *, timeout: int = 30) -> LLMResult:
        return self._invoke(
            [{"role": "user", "content": prompt}],
            system="You are an API integration expert. Respond with only valid JSON. No markdown fences, no explanation text, just the JSON object.",
            max_tokens=1024,
            timeout=timeout,
        )

    def _invoke(self, messages: list[dict], *, system: str, max_tokens: int = 1024, timeout: int = 30) -> LLMResult:
        try:
            import hashlib
            import hmac
            from datetime import datetime, timezone
            from urllib.parse import quote

            import httpx
        except ImportError:
            return LLMResult(text="", success=False, error="httpx not installed")

        # Always use the Lanrey account's Bedrock region (us-east-1),
        # ignoring any ambient AWS_REGION in the shell environment.
        region = os.environ.get("PIPELINE_AWS_REGION", _AWS_REGION_DEFAULT)
        model = os.environ.get("PIPELINE_LLM_MODEL", _MODEL_DEFAULT)

        # Resolve credentials: explicit env vars → named profile
        access_key = os.environ.get("AWS_ACCESS_KEY_ID", "")
        secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
        session_token = os.environ.get("AWS_SESSION_TOKEN")

        if not access_key or not secret_key:
            profile = os.environ.get("AWS_PROFILE", _AWS_PROFILE_DEFAULT)
            try:
                access_key, secret_key, session_token = _load_aws_profile_credentials(profile)
            except RuntimeError as e:
                return LLMResult(text="", success=False, error=str(e))

        host = f"bedrock-runtime.{region}.amazonaws.com"
        # Model IDs may contain colons (e.g. "v1:0") — encode everything
        encoded_model = quote(model, safe="")
        path = f"/model/{encoded_model}/invoke"
        url = f"https://{host}{path}"

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "messages": messages,
            "system": system,
        })

        now = datetime.now(timezone.utc)
        datestamp = now.strftime("%Y%m%d")
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        service = "bedrock"

        headers_to_sign = {
            "content-type": "application/json",
            "host": host,
            "x-amz-date": amz_date,
        }
        if session_token:
            headers_to_sign["x-amz-security-token"] = session_token

        signed_header_keys = ";".join(sorted(headers_to_sign.keys()))
        canonical_headers = "".join(
            f"{k}:{v}\n" for k, v in sorted(headers_to_sign.items())
        )
        payload_hash = hashlib.sha256(body.encode()).hexdigest()

        canonical_request = "\n".join([
            "POST",
            path,
            "",  # empty query string
            canonical_headers,
            signed_header_keys,
            payload_hash,
        ])

        scope = f"{datestamp}/{region}/{service}/aws4_request"
        string_to_sign = "\n".join([
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ])

        def _sign(key: bytes, msg: str) -> bytes:
            return hmac.HMAC(key, msg.encode(), hashlib.sha256).digest()

        k_date = _sign(f"AWS4{secret_key}".encode(), datestamp)
        k_region = _sign(k_date, region)
        k_service = _sign(k_region, service)
        k_signing = _sign(k_service, "aws4_request")
        signature = hmac.HMAC(
            k_signing, string_to_sign.encode(), hashlib.sha256
        ).hexdigest()

        auth_header = (
            f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
            f"SignedHeaders={signed_header_keys}, Signature={signature}"
        )

        request_headers = {
            "Content-Type": "application/json",
            "X-Amz-Date": amz_date,
            "Authorization": auth_header,
        }
        if session_token:
            request_headers["X-Amz-Security-Token"] = session_token

        try:
            response = httpx.post(
                url, headers=request_headers, content=body, timeout=timeout
            )
            response.raise_for_status()
            data = response.json()
            # Bedrock returns the same Anthropic Messages response format:
            # {"content": [{"type": "text", "text": "..."}], ...}
            content_blocks = data.get("content", [])
            text_parts = []
            for block in content_blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            text = "\n".join(text_parts).strip()
            return LLMResult(text=text, parsed=_try_parse_json(text), success=True)
        except httpx.HTTPStatusError as e:
            error_body = e.response.text[:500] if e.response else str(e)
            return LLMResult(text="", success=False, error=f"Bedrock HTTP {e.response.status_code}: {error_body}")
        except Exception as e:
            return LLMResult(text="", success=False, error=str(e))


# --- Provider Registry ---

PROVIDERS: dict[str, type[LLMProvider]] = {
    "bedrock": BedrockProvider,
}

_active_provider: LLMProvider | None = None


def get_llm_provider() -> LLMProvider | None:
    """Get the active LLM provider (always AWS Bedrock / Claude)."""
    global _active_provider
    if _active_provider is not None:
        return _active_provider

    provider = BedrockProvider()
    if provider.is_available():
        _active_provider = provider
        logger.info("llm_provider_selected", provider="bedrock", model=_MODEL_DEFAULT)
        return _active_provider

    logger.warning("llm_bedrock_unavailable", hint="Check AWS credentials for profile 'Lanrey'")
    return None


def set_llm_provider(name: str) -> bool:
    """No-op compatibility shim — only Bedrock is supported."""
    global _active_provider
    if name.lower() != "bedrock":
        logger.warning("llm_provider_unsupported", requested=name, supported="bedrock")
        return False
    provider = BedrockProvider()
    if not provider.is_available():
        return False
    _active_provider = provider
    return True


def available_providers() -> list[dict[str, Any]]:
    """Return availability status for the single supported provider."""
    p = BedrockProvider()
    return [{"name": "bedrock", "available": p.is_available()}]


# --- Utility ---

def _try_parse_json(text: str) -> dict[str, Any] | None:
    """Parse JSON from Claude's response, handling common output quirks.

    Claude via Bedrock may occasionally:
    - Wrap JSON in ```json ... ``` markdown fences
    - Add a brief explanatory prefix before the JSON object
    - Return the JSON cleanly (the expected case given the system prompt)
    """
    text = text.strip()
    if not text:
        return None

    # Strip markdown fences if present (```json ... ``` or ``` ... ```)
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```) and last line (```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    # Try direct parse first (cleanest case)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    # Find the outermost JSON object by matching braces
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    end = -1
    in_string = False
    escape_next = False

    for i in range(start, len(text)):
        c = text[i]
        if escape_next:
            escape_next = False
            continue
        if c == "\\":
            escape_next = True
            continue
        if c == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    # Last resort: find first { and last }
    last_brace = text.rfind("}")
    if last_brace > start:
        try:
            return json.loads(text[start:last_brace + 1])
        except json.JSONDecodeError:
            pass

    return None


def _run_llm(prompt: str, *, timeout: int = 30) -> LLMResult:
    """Run a prompt through the active LLM provider."""
    provider = get_llm_provider()
    if provider is None:
        return LLMResult(text="", success=False, error="no LLM provider available")
    return provider.complete(prompt, timeout=timeout)


# --- Public API ---


def plan_action(
    request: str,
    provider: str,
    capabilities: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Use LLM to understand what API call to make for a natural language request.

    Given a request like "get all open issues", determines the HTTP method,
    path, params, and body needed.

    Returns a dict with keys: method, path, params, body, description
    or None if LLM is unavailable / fails.
    """
    caps_str = json.dumps(capabilities or {}, indent=2)
    prompt = (
        "Given this request and provider info, determine the exact API call to make.\n\n"
        f"Provider: {provider}\n"
        f"Known capabilities: {caps_str}\n"
        f"User request: {request}\n\n"
        "Respond with ONLY a JSON object:\n"
        '{"method": "GET or POST", "path": "/the/endpoint/path", '
        '"params": {}, "body": null, "description": "brief description"}'
    )

    result = _run_llm(prompt)
    if not result.success:
        logger.debug("plan_action_fallback", provider=provider, error=result.error)
        return None

    if result.parsed and isinstance(result.parsed, dict):
        return result.parsed

    return _try_parse_json(result.text)


def heal_failure(
    request: str,
    error: str,
    params: dict[str, Any],
) -> dict[str, Any] | None:
    """Use LLM to fix a failed API call by generating parameter patches.

    Given a failure and the original params, generates corrected params.

    Returns a dict with corrected params, or None if unavailable.
    """
    prompt = (
        "An API call failed. Analyze the error and suggest corrected parameters.\n\n"
        f"Original request: {request}\n"
        f"Parameters used: {json.dumps(params, indent=2)}\n"
        f"Error received: {error}\n\n"
        "Respond with ONLY a JSON object containing the corrected parameters:\n"
        '{"method": "...", "path": "...", "params": {}, "body": null, '
        '"fix_description": "what was wrong and what you changed"}'
    )

    result = _run_llm(prompt)
    if not result.success:
        return None
    if result.parsed and isinstance(result.parsed, dict):
        return result.parsed
    return _try_parse_json(result.text)


def discover_auth_docs(provider: str) -> dict[str, Any] | None:
    """Use LLM to find authentication documentation for a provider.

    Returns a dict with: auth_type, auth_header, auth_prefix, docs_url, instructions
    or None if unavailable.
    """
    prompt = (
        f'Find the authentication documentation for the "{provider}" API.\n\n'
        "Respond with ONLY a JSON object:\n"
        '{"auth_type": "api_key|oauth2|basic|none", '
        '"auth_header": "Authorization or other header name", '
        '"auth_prefix": "Bearer or other prefix (empty string if none)", '
        '"docs_url": "URL to the auth documentation", '
        '"instructions": "Brief step-by-step to get credentials"}'
    )

    result = _run_llm(prompt, timeout=20)
    if not result.success:
        return None
    if result.parsed and isinstance(result.parsed, dict):
        return result.parsed
    return _try_parse_json(result.text)


def discover_provider_config(provider: str) -> dict[str, Any] | None:
    """Use LLM to discover full connection config for an unknown provider.

    Returns a dict with: base_url, auth_type, pagination_style, endpoints, etc.
    or None if unavailable.
    """
    prompt = (
        f'Provide the connection configuration for the "{provider}" API.\n\n'
        "Respond with ONLY a JSON object:\n"
        '{"base_url": "https://api.example.com", '
        '"auth_type": "api_key|oauth2|basic", '
        '"auth_header": "Authorization", '
        '"auth_prefix": "Bearer", '
        '"pagination_style": "cursor|offset|link_header|graphql_cursor", '
        '"api_style": "rest|graphql", '
        '"default_endpoints": {"resource_name": "/path/to/resource"}, '
        '"docs_url": "https://docs.example.com", '
        '"rate_limit_rpm": 100}'
    )

    result = _run_llm(prompt, timeout=20)
    if not result.success:
        return None
    if result.parsed and isinstance(result.parsed, dict):
        return result.parsed
    return _try_parse_json(result.text)
