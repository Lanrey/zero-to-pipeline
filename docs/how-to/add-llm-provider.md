# How to add a new LLM provider

This guide is for contributors who want to add support for a new LLM provider to zero-pipeline.

## Prerequisites

- Familiarity with the target LLM API's request/response format
- A working development environment (`uv sync --extra dev`)

## Step 1 — Implement the provider class

Create a new class in `src/data_pipeline/connectors/llm_discovery.py` that extends `LLMProvider`:

```python
class MyProvider(LLMProvider):
    @property
    def name(self) -> str:
        return "myprovider"

    def _model(self) -> str:
        return settings.llm_model or "default-model-id"

    def _base_url(self) -> str:
        return settings.llm_base_url or "https://api.myprovider.com"

    def is_available(self) -> bool:
        return _get_api_key("myprovider") is not None

    def chat(
        self,
        messages: list[dict],
        *,
        system: str,
        max_tokens: int = 2048,
        timeout: int = 60,
    ) -> LLMResult:
        return self._invoke(
            messages, system=system, max_tokens=max_tokens, timeout=timeout
        )

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
        import httpx

        api_key = _get_api_key("myprovider")
        if not api_key:
            return LLMResult(
                text="", success=False, error="No myprovider API key found"
            )

        # Build request body per the provider's API format
        body = json.dumps({...})
        headers = {"Authorization": f"Bearer {api_key}", ...}

        try:
            resp = httpx.post(
                f"{self._base_url()}/v1/completions",
                headers=headers,
                content=body,
                timeout=timeout,
            )
            resp.raise_for_status()
            # Parse response per the provider's format
            text = ...
            return LLMResult(
                text=text, parsed=_try_parse_json(text), success=True
            )
        except httpx.HTTPStatusError as e:
            body_text = e.response.text[:500] if e.response else str(e)
            return LLMResult(
                text="",
                success=False,
                error=f"HTTP {e.response.status_code}: {body_text}",
            )
        except Exception as e:
            return LLMResult(text="", success=False, error=str(e))
```

## Step 2 — Register the provider

Add it to the `PROVIDERS` dict and the default model map:

```python
_DEFAULT_MODELS["myprovider"] = "default-model-id"
_DEFAULT_BASE_URLS["myprovider"] = "https://api.myprovider.com"

PROVIDERS: dict[str, type[LLMProvider]] = {
    "openai": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
    "myprovider": MyProvider,
}
```

## Step 3 — Add the env var fallback

In `_get_api_key()`, add the env var mapping:

```python
env_map = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "myprovider": "MYPROVIDER_API_KEY",
}
```

## Step 4 — Write tests

Add a test class in `tests/test_llm_providers.py` following the existing pattern:

```python
class TestMyProvider:
    @respx.mock
    def test_complete_success(self):
        respx.post("https://api.myprovider.com/v1/completions").mock(
            return_value=httpx.Response(200, json={...})
        )
        with patch(
            "data_pipeline.connectors.llm_discovery._get_api_key",
            return_value="test-key",
        ):
            provider = MyProvider()
            result = provider.complete("test")
            assert result.success
```

## Step 5 — Verify

```bash
pytest tests/test_llm_providers.py -v
ruff check src/data_pipeline/connectors/llm_discovery.py
```

Users select the new provider with:

```bash
export PIPELINE_LLM_PROVIDER=myprovider
pipeline auth set myprovider --token ...
```

## See also

- [Reference: LLM provider interface](../reference.md#llm-provider-interface)
- [Architecture: BYOK model-agnostic LLM layer](../explanation.md#byok-model-agnostic-llm-layer)
