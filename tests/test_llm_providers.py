"""Tests for the model-agnostic LLM provider layer."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import respx

from data_pipeline.connectors.llm_discovery import (
    AnthropicProvider,
    OpenAICompatibleProvider,
    _get_api_key,
    _try_parse_json,
    available_providers,
    get_llm_provider,
    set_llm_provider,
)

# --- _get_api_key ---


class TestGetApiKey:
    def test_keychain_takes_priority(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "env-key")
        with patch("data_pipeline.auth.credential_store.CredentialStore") as mock_cls:
            mock_cls.return_value.retrieve.return_value = {"access_token": "keychain-key"}
            assert _get_api_key("openai") == "keychain-key"

    def test_env_var_fallback(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        with patch("data_pipeline.auth.credential_store.CredentialStore") as mock_cls:
            mock_cls.return_value.retrieve.return_value = None
            assert _get_api_key("openai") == "sk-test"

    def test_anthropic_env_var(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        with patch("data_pipeline.auth.credential_store.CredentialStore") as mock_cls:
            mock_cls.return_value.retrieve.return_value = None
            assert _get_api_key("anthropic") == "sk-ant-test"

    def test_no_key_returns_none(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with patch("data_pipeline.auth.credential_store.CredentialStore") as mock_cls:
            mock_cls.return_value.retrieve.return_value = None
            assert _get_api_key("openai") is None


# --- OpenAICompatibleProvider ---


class TestOpenAICompatibleProvider:
    @respx.mock
    def test_complete_success(self):
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": '{"key": "val"}'}}],
                },
            )
        )
        with patch("data_pipeline.connectors.llm_discovery._get_api_key", return_value="sk-test"):
            provider = OpenAICompatibleProvider()
            result = provider.complete("test prompt")
            assert result.success
            assert result.parsed == {"key": "val"}

    @respx.mock
    def test_complete_http_error(self):
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(401, json={"error": "invalid_api_key"})
        )
        with patch("data_pipeline.connectors.llm_discovery._get_api_key", return_value="sk-bad"):
            provider = OpenAICompatibleProvider()
            result = provider.complete("test")
            assert not result.success
            assert "401" in result.error

    @respx.mock
    def test_chat_sends_full_history(self):
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "response"}}],
                },
            )
        )
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "bye"},
        ]
        with patch("data_pipeline.connectors.llm_discovery._get_api_key", return_value="sk-test"):
            provider = OpenAICompatibleProvider()
            result = provider.chat(messages, system="test system")
            assert result.success

            import json

            sent_body = json.loads(route.calls[0].request.content)
            assert sent_body["messages"][0] == {"role": "system", "content": "test system"}
            assert sent_body["messages"][1:] == messages

    @respx.mock
    def test_custom_base_url(self, monkeypatch):
        monkeypatch.setattr(
            "data_pipeline.connectors.llm_discovery.settings.llm_base_url", "http://localhost:11434"
        )
        respx.post("http://localhost:11434/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "local response"}}],
                },
            )
        )
        with patch("data_pipeline.connectors.llm_discovery._get_api_key", return_value=None):
            provider = OpenAICompatibleProvider()
            result = provider.complete("test")
            assert result.success
            assert result.text == "local response"

    def test_available_without_key_when_custom_url(self, monkeypatch):
        monkeypatch.setattr(
            "data_pipeline.connectors.llm_discovery.settings.llm_base_url", "http://localhost:11434"
        )
        with patch("data_pipeline.connectors.llm_discovery._get_api_key", return_value=None):
            provider = OpenAICompatibleProvider()
            assert provider.is_available()

    def test_not_available_without_key_default_url(self, monkeypatch):
        monkeypatch.setattr("data_pipeline.connectors.llm_discovery.settings.llm_base_url", "")
        with patch("data_pipeline.connectors.llm_discovery._get_api_key", return_value=None):
            provider = OpenAICompatibleProvider()
            assert not provider.is_available()

    @respx.mock
    def test_no_auth_header_when_no_key(self, monkeypatch):
        monkeypatch.setattr(
            "data_pipeline.connectors.llm_discovery.settings.llm_base_url", "http://localhost:11434"
        )
        route = respx.post("http://localhost:11434/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "ok"}}],
                },
            )
        )
        with patch("data_pipeline.connectors.llm_discovery._get_api_key", return_value=None):
            provider = OpenAICompatibleProvider()
            provider.complete("test")
            assert "Authorization" not in route.calls[0].request.headers


# --- AnthropicProvider ---


class TestAnthropicProvider:
    @respx.mock
    def test_complete_success(self):
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": '{"key": "val"}'}],
                },
            )
        )
        with patch(
            "data_pipeline.connectors.llm_discovery._get_api_key", return_value="sk-ant-test"
        ):
            provider = AnthropicProvider()
            result = provider.complete("test prompt")
            assert result.success
            assert result.parsed == {"key": "val"}

    @respx.mock
    def test_sends_correct_headers(self):
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": "ok"}],
                },
            )
        )
        with patch(
            "data_pipeline.connectors.llm_discovery._get_api_key", return_value="sk-ant-test"
        ):
            provider = AnthropicProvider()
            provider.complete("test")
            headers = route.calls[0].request.headers
            assert headers["x-api-key"] == "sk-ant-test"
            assert headers["anthropic-version"] == "2023-06-01"
            assert headers["content-type"] == "application/json"

    @respx.mock
    def test_complete_http_error(self):
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(403, json={"error": "forbidden"})
        )
        with patch(
            "data_pipeline.connectors.llm_discovery._get_api_key", return_value="sk-ant-bad"
        ):
            provider = AnthropicProvider()
            result = provider.complete("test")
            assert not result.success
            assert "403" in result.error

    @respx.mock
    def test_chat_includes_system_field(self):
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": "response"}],
                },
            )
        )
        with patch(
            "data_pipeline.connectors.llm_discovery._get_api_key", return_value="sk-ant-test"
        ):
            provider = AnthropicProvider()
            provider.chat(
                [{"role": "user", "content": "hello"}],
                system="test system",
            )
            import json

            sent_body = json.loads(route.calls[0].request.content)
            assert sent_body["system"] == "test system"
            assert sent_body["messages"] == [{"role": "user", "content": "hello"}]

    def test_not_available_without_key(self):
        with patch("data_pipeline.connectors.llm_discovery._get_api_key", return_value=None):
            provider = AnthropicProvider()
            assert not provider.is_available()


# --- Provider Selection ---


class TestProviderSelection:
    def test_default_is_openai(self, monkeypatch):
        monkeypatch.setattr(
            "data_pipeline.connectors.llm_discovery.settings.llm_provider", "openai"
        )
        monkeypatch.setattr("data_pipeline.connectors.llm_discovery._active_provider", None)
        with patch("data_pipeline.connectors.llm_discovery._get_api_key", return_value="sk-test"):
            provider = get_llm_provider()
            assert provider is not None
            assert provider.name == "openai"

    def test_anthropic_selection(self, monkeypatch):
        monkeypatch.setattr(
            "data_pipeline.connectors.llm_discovery.settings.llm_provider", "anthropic"
        )
        monkeypatch.setattr("data_pipeline.connectors.llm_discovery._active_provider", None)
        with patch(
            "data_pipeline.connectors.llm_discovery._get_api_key", return_value="sk-ant-test"
        ):
            provider = get_llm_provider()
            assert provider is not None
            assert provider.name == "anthropic"

    def test_unknown_provider_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            "data_pipeline.connectors.llm_discovery.settings.llm_provider", "nonexistent"
        )
        monkeypatch.setattr("data_pipeline.connectors.llm_discovery._active_provider", None)
        assert get_llm_provider() is None

    def test_set_llm_provider(self, monkeypatch):
        monkeypatch.setattr("data_pipeline.connectors.llm_discovery._active_provider", None)
        with patch(
            "data_pipeline.connectors.llm_discovery._get_api_key", return_value="sk-ant-test"
        ):
            assert set_llm_provider("anthropic")

    def test_available_providers_lists_both(self):
        with patch("data_pipeline.connectors.llm_discovery._get_api_key", return_value=None):
            providers = available_providers()
            names = {p["name"] for p in providers}
            assert names == {"openai", "anthropic"}


# --- _try_parse_json ---


class TestTryParseJson:
    def test_clean_json(self):
        assert _try_parse_json('{"key": "val"}') == {"key": "val"}

    def test_markdown_fenced(self):
        text = '```json\n{"key": "val"}\n```'
        assert _try_parse_json(text) == {"key": "val"}

    def test_text_with_embedded_json(self):
        text = 'Here is the result: {"key": "val"} and some trailing text'
        assert _try_parse_json(text) == {"key": "val"}

    def test_empty_string(self):
        assert _try_parse_json("") is None

    def test_no_json(self):
        assert _try_parse_json("just plain text") is None

    def test_list_returns_none(self):
        assert _try_parse_json("[1, 2, 3]") is None

    def test_nested_json(self):
        text = '{"outer": {"inner": "val"}}'
        result = _try_parse_json(text)
        assert result == {"outer": {"inner": "val"}}
