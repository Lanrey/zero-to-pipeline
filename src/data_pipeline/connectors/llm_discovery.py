from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import structlog

from data_pipeline.config import settings

logger = structlog.get_logger(__name__)

_DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-20250514",
}

_DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com",
    "anthropic": "https://api.anthropic.com",
}


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

    def chat(
        self, messages: list[dict], *, system: str, max_tokens: int = 2048, timeout: int = 60
    ) -> LLMResult:
        """Multi-turn chat using full message history. Default impl wraps complete()."""
        last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return self.complete(last, timeout=timeout)


def _get_api_key(provider_name: str) -> str | None:
    """Resolve API key: CredentialStore -> well-known env var -> None."""
    from data_pipeline.auth.credential_store import CredentialStore

    store = CredentialStore()
    cred = store.retrieve(provider_name)
    if cred:
        key = cred.get("access_token") or cred.get("api_key")
        if key:
            return key

    env_map = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }
    env_var = env_map.get(provider_name)
    if env_var:
        return os.environ.get(env_var)

    return None


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI-compatible provider — works with OpenAI, Azure, Groq, Together,
    Ollama, vLLM, LM Studio, and any server implementing the chat completions API.

    Users point at any OpenAI-compatible endpoint by setting PIPELINE_LLM_BASE_URL.
    Local servers (Ollama, vLLM) work without an API key.
    """

    @property
    def name(self) -> str:
        return "openai"

    def _model(self) -> str:
        return settings.llm_model or _DEFAULT_MODELS["openai"]

    def _base_url(self) -> str:
        return settings.llm_base_url or _DEFAULT_BASE_URLS["openai"]

    def _is_custom_url(self) -> bool:
        return bool(settings.llm_base_url) and settings.llm_base_url != _DEFAULT_BASE_URLS["openai"]

    def is_available(self) -> bool:
        if _get_api_key("openai"):
            return True
        return self._is_custom_url()

    def chat(
        self, messages: list[dict], *, system: str, max_tokens: int = 2048, timeout: int = 60
    ) -> LLMResult:
        return self._invoke(messages, system=system, max_tokens=max_tokens, timeout=timeout)

    def complete(self, prompt: str, *, timeout: int = 30) -> LLMResult:
        return self._invoke(
            [{"role": "user", "content": prompt}],
            system=(
                "You are an API integration expert. Respond with only valid JSON."
                " No markdown fences, no explanation text, just the JSON object."
            ),
            max_tokens=1024,
            timeout=timeout,
        )

    def _invoke(
        self,
        messages: list[dict],
        *,
        system: str,
        max_tokens: int = 1024,
        timeout: int = 30,
    ) -> LLMResult:
        try:
            import httpx
        except ImportError:
            return LLMResult(text="", success=False, error="httpx not installed")

        api_key = _get_api_key("openai")
        base = self._base_url().rstrip("/")
        url = f"{base}/v1/chat/completions"

        all_messages = [{"role": "system", "content": system}, *messages]
        body = json.dumps(
            {
                "model": self._model(),
                "messages": all_messages,
                "max_tokens": max_tokens,
            }
        )

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            resp = httpx.post(url, headers=headers, content=body, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"].strip()
            return LLMResult(text=text, parsed=_try_parse_json(text), success=True)
        except httpx.HTTPStatusError as e:
            body_text = e.response.text[:500] if e.response else str(e)
            return LLMResult(
                text="",
                success=False,
                error=f"HTTP {e.response.status_code}: {body_text}",
            )
        except Exception as e:
            return LLMResult(text="", success=False, error=str(e))


class AnthropicProvider(LLMProvider):
    """Native Anthropic Messages API provider.

    Uses the Anthropic API directly with x-api-key authentication.
    """

    @property
    def name(self) -> str:
        return "anthropic"

    def _model(self) -> str:
        return settings.llm_model or _DEFAULT_MODELS["anthropic"]

    def _base_url(self) -> str:
        return settings.llm_base_url or _DEFAULT_BASE_URLS["anthropic"]

    def is_available(self) -> bool:
        return _get_api_key("anthropic") is not None

    def chat(
        self, messages: list[dict], *, system: str, max_tokens: int = 2048, timeout: int = 60
    ) -> LLMResult:
        return self._invoke(messages, system=system, max_tokens=max_tokens, timeout=timeout)

    def complete(self, prompt: str, *, timeout: int = 30) -> LLMResult:
        return self._invoke(
            [{"role": "user", "content": prompt}],
            system=(
                "You are an API integration expert. Respond with only valid JSON."
                " No markdown fences, no explanation text, just the JSON object."
            ),
            max_tokens=1024,
            timeout=timeout,
        )

    def _invoke(
        self,
        messages: list[dict],
        *,
        system: str,
        max_tokens: int = 1024,
        timeout: int = 30,
    ) -> LLMResult:
        try:
            import httpx
        except ImportError:
            return LLMResult(text="", success=False, error="httpx not installed")

        api_key = _get_api_key("anthropic")
        if not api_key:
            return LLMResult(
                text="", success=False, error="No Anthropic API key found"
            )

        base = self._base_url().rstrip("/")
        url = f"{base}/v1/messages"
        body = json.dumps(
            {
                "model": self._model(),
                "max_tokens": max_tokens,
                "system": system,
                "messages": messages,
            }
        )

        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }

        try:
            resp = httpx.post(url, headers=headers, content=body, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            content_blocks = data.get("content", [])
            text_parts = []
            for block in content_blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            text = "\n".join(text_parts).strip()
            return LLMResult(text=text, parsed=_try_parse_json(text), success=True)
        except httpx.HTTPStatusError as e:
            body_text = e.response.text[:500] if e.response else str(e)
            return LLMResult(
                text="",
                success=False,
                error=f"HTTP {e.response.status_code}: {body_text}",
            )
        except Exception as e:
            return LLMResult(text="", success=False, error=str(e))


# --- Provider Registry ---

PROVIDERS: dict[str, type[LLMProvider]] = {
    "openai": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
}

_active_provider: LLMProvider | None = None


def get_llm_provider() -> LLMProvider | None:
    """Get the active LLM provider based on PIPELINE_LLM_PROVIDER setting."""
    global _active_provider
    if _active_provider is not None:
        return _active_provider

    provider_name = settings.llm_provider.lower()
    cls = PROVIDERS.get(provider_name)
    if cls is None:
        logger.warning(
            "llm_provider_unknown", requested=provider_name, supported=list(PROVIDERS.keys())
        )
        return None

    provider = cls()
    if provider.is_available():
        _active_provider = provider
        model = settings.llm_model or "(default)"
        logger.info("llm_provider_selected", provider=provider_name, model=model)
        return _active_provider

    logger.warning(
        "llm_provider_unavailable",
        provider=provider_name,
        hint=f"Run: pipeline auth set {provider_name}",
    )
    return None


def set_llm_provider(name: str) -> bool:
    """Set the active LLM provider by name."""
    global _active_provider
    cls = PROVIDERS.get(name.lower())
    if cls is None:
        logger.warning(
            "llm_provider_unsupported",
            requested=name,
            supported=list(PROVIDERS.keys()),
        )
        return False
    provider = cls()
    if not provider.is_available():
        return False
    _active_provider = provider
    return True


def available_providers() -> list[dict[str, Any]]:
    """Return availability status for all supported providers."""
    return [
        {"name": name, "available": cls().is_available()}
        for name, cls in PROVIDERS.items()
    ]


# --- Utility ---


def _try_parse_json(text: str) -> dict[str, Any] | None:
    """Parse JSON from an LLM response, handling common output quirks.

    LLMs may occasionally:
    - Wrap JSON in ```json ... ``` markdown fences
    - Add a brief explanatory prefix before the JSON object
    - Return the JSON cleanly (the expected case given the system prompt)
    """
    text = text.strip()
    if not text:
        return None

    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

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

    last_brace = text.rfind("}")
    if last_brace > start:
        try:
            return json.loads(text[start : last_brace + 1])
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
    """Use LLM to understand what API call to make for a natural language request."""
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
    """Use LLM to fix a failed API call by generating parameter patches."""
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
    """Use LLM to find authentication documentation for a provider."""
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
    """Use LLM to discover full connection config for an unknown provider."""
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
