"""LLM-driven API discovery with pluggable providers.

Supports multiple LLM backends:
- copilot: GitHub Copilot CLI (legacy, default if available)
- openai: OpenAI API (GPT-4o, etc.)
- anthropic: Anthropic API (Claude)
- ollama: Local Ollama models

The provider is selected via:
1. PIPELINE_LLM_PROVIDER env var
2. pipeline config (settings)
3. Auto-detection (tries copilot → openai → anthropic → ollama)

When no LLM is available, all functions gracefully fall back to None
and the system uses registry + inference patterns.
"""

from __future__ import annotations

import json
import os
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import structlog

from data_pipeline.config import settings

logger = structlog.get_logger(__name__)


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


class CopilotProvider(LLMProvider):
    """GitHub Copilot CLI provider (legacy)."""

    _available: bool | None = None

    @property
    def name(self) -> str:
        return "copilot"

    def is_available(self) -> bool:
        if CopilotProvider._available is not None:
            return CopilotProvider._available
        try:
            result = subprocess.run(
                ["copilot", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            CopilotProvider._available = result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            CopilotProvider._available = False
        return CopilotProvider._available

    def complete(self, prompt: str, *, timeout: int = 30) -> LLMResult:
        model = os.environ.get("PIPELINE_LLM_MODEL", "gpt-5.3-codex")
        command = [
            "copilot", "-p", prompt,
            "--model", model,
            "--allow-all-tools",
            "--stream", "off",
            "--silent", "--no-color",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
                env=os.environ.copy(),
            )
            stdout_text = (result.stdout or "").strip()
            if result.returncode != 0:
                stderr_text = (result.stderr or "").strip()
                return LLMResult(text=stdout_text, success=False, error=stderr_text or f"exit code {result.returncode}")
            return LLMResult(text=stdout_text, parsed=_try_parse_json(stdout_text), success=True)
        except FileNotFoundError:
            return LLMResult(text="", success=False, error="copilot CLI not found")
        except subprocess.TimeoutExpired:
            return LLMResult(text="", success=False, error=f"timed out after {timeout}s")
        except Exception as e:
            return LLMResult(text="", success=False, error=str(e))


class OpenAIProvider(LLMProvider):
    """OpenAI API provider."""

    @property
    def name(self) -> str:
        return "openai"

    def is_available(self) -> bool:
        return bool(os.environ.get("OPENAI_API_KEY"))

    def complete(self, prompt: str, *, timeout: int = 30) -> LLMResult:
        try:
            import httpx
        except ImportError:
            return LLMResult(text="", success=False, error="httpx not installed")

        api_key = os.environ.get("OPENAI_API_KEY", "")
        model = os.environ.get("PIPELINE_LLM_MODEL", "gpt-4o")
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

        try:
            response = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "You are an API integration expert. Respond with only JSON, no markdown."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            text = data["choices"][0]["message"]["content"].strip()
            return LLMResult(text=text, parsed=_try_parse_json(text), success=True)
        except Exception as e:
            return LLMResult(text="", success=False, error=str(e))


class AnthropicProvider(LLMProvider):
    """Anthropic direct API provider."""

    @property
    def name(self) -> str:
        return "anthropic"

    def is_available(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    def complete(self, prompt: str, *, timeout: int = 30) -> LLMResult:
        try:
            import httpx
        except ImportError:
            return LLMResult(text="", success=False, error="httpx not installed")

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        model = os.environ.get("PIPELINE_LLM_MODEL", "claude-sonnet-4-20250514")

        try:
            response = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": prompt}],
                    "system": "You are an API integration expert. Respond with only valid JSON. No markdown fences, no explanation text, just the JSON object.",
                },
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            # Anthropic Messages API returns: {"content": [{"type": "text", "text": "..."}]}
            content_blocks = data.get("content", [])
            text_parts = []
            for block in content_blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            text = "\n".join(text_parts).strip()
            return LLMResult(text=text, parsed=_try_parse_json(text), success=True)
        except httpx.HTTPStatusError as e:
            error_body = e.response.text[:500] if e.response else str(e)
            return LLMResult(text="", success=False, error=f"Anthropic HTTP {e.response.status_code}: {error_body}")
        except Exception as e:
            return LLMResult(text="", success=False, error=str(e))


class BedrockProvider(LLMProvider):
    """AWS Bedrock provider for Anthropic Claude models.

    Requires AWS credentials configured via:
    - AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (+ optional AWS_SESSION_TOKEN)
    - Or AWS_PROFILE with credentials in ~/.aws/credentials
    - AWS_REGION (defaults to us-east-1)

    Set PIPELINE_LLM_PROVIDER=bedrock to use.
    """

    @property
    def name(self) -> str:
        return "bedrock"

    def is_available(self) -> bool:
        has_keys = bool(
            os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")
        )
        has_profile = bool(os.environ.get("AWS_PROFILE"))
        return has_keys or has_profile

    def complete(self, prompt: str, *, timeout: int = 30) -> LLMResult:
        try:
            import hashlib
            import hmac
            from datetime import datetime, timezone
            from urllib.parse import quote

            import httpx
        except ImportError:
            return LLMResult(text="", success=False, error="httpx not installed")

        region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        model = os.environ.get("PIPELINE_LLM_MODEL", "anthropic.claude-sonnet-4-20250514-v1:0")
        access_key = os.environ.get("AWS_ACCESS_KEY_ID", "")
        secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
        session_token = os.environ.get("AWS_SESSION_TOKEN")

        if not access_key or not secret_key:
            return LLMResult(
                text="", success=False,
                error="AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY required",
            )

        host = f"bedrock-runtime.{region}.amazonaws.com"
        # Model IDs contain colons (e.g. "v1:0") — must be URL-encoded in the path
        encoded_model = quote(model, safe="")
        path = f"/model/{encoded_model}/invoke"
        url = f"https://{host}{path}"

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
            "system": "You are an API integration expert. Respond with only valid JSON. No markdown fences, no explanation text, just the JSON object.",
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


class OllamaProvider(LLMProvider):
    """Local Ollama provider."""

    @property
    def name(self) -> str:
        return "ollama"

    def is_available(self) -> bool:
        try:
            import httpx
            response = httpx.get(
                os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434") + "/api/tags",
                timeout=3,
            )
            return response.status_code == 200
        except Exception:
            return False

    def complete(self, prompt: str, *, timeout: int = 60) -> LLMResult:
        try:
            import httpx
        except ImportError:
            return LLMResult(text="", success=False, error="httpx not installed")

        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        model = os.environ.get("PIPELINE_LLM_MODEL", "llama3.1")

        try:
            response = httpx.post(
                f"{base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": f"You are an API integration expert. Respond with only JSON.\n\n{prompt}",
                    "stream": False,
                    "format": "json",
                },
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            text = data.get("response", "").strip()
            return LLMResult(text=text, parsed=_try_parse_json(text), success=True)
        except Exception as e:
            return LLMResult(text="", success=False, error=str(e))


# --- Provider Registry ---

PROVIDERS: dict[str, type[LLMProvider]] = {
    "copilot": CopilotProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "bedrock": BedrockProvider,
    "ollama": OllamaProvider,
}

_active_provider: LLMProvider | None = None


def get_llm_provider() -> LLMProvider | None:
    """Get the active LLM provider.

    Selection order:
    1. PIPELINE_LLM_PROVIDER env var (explicit choice)
    2. Auto-detect first available: copilot → openai → anthropic → ollama
    """
    global _active_provider
    if _active_provider is not None:
        return _active_provider

    explicit = os.environ.get("PIPELINE_LLM_PROVIDER", "").lower().strip()
    if explicit and explicit in PROVIDERS:
        provider = PROVIDERS[explicit]()
        if provider.is_available():
            _active_provider = provider
            logger.info("llm_provider_selected", provider=explicit, source="env")
            return _active_provider
        logger.warning("llm_provider_unavailable", provider=explicit)

    for name, cls in PROVIDERS.items():
        provider = cls()
        if provider.is_available():
            _active_provider = provider
            logger.info("llm_provider_selected", provider=name, source="auto")
            return _active_provider

    logger.info("llm_no_provider_available")
    return None


def set_llm_provider(name: str) -> bool:
    """Explicitly set the LLM provider by name. Returns True if available."""
    global _active_provider
    if name.lower() not in PROVIDERS:
        return False
    provider = PROVIDERS[name.lower()]()
    if not provider.is_available():
        return False
    _active_provider = provider
    return True


def available_providers() -> list[dict[str, Any]]:
    """List all providers and their availability status."""
    result = []
    for name, cls in PROVIDERS.items():
        p = cls()
        result.append({"name": name, "available": p.is_available()})
    return result


# --- Utility ---

def _try_parse_json(text: str) -> dict[str, Any] | None:
    """Parse JSON from LLM output, handling common response quirks.

    LLMs (especially Claude via Bedrock/Anthropic) may:
    - Wrap JSON in ```json ... ``` markdown fences
    - Add explanatory text before/after the JSON
    - Return the JSON cleanly (ideal case)

    This parser handles all these patterns uniformly regardless of provider.
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
