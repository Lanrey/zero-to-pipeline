"""Tests for authentication module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from data_pipeline.auth.credential_store import CredentialStore
from data_pipeline.auth.manager import AuthManager
from data_pipeline.schemas import AuthType, OAuthConfig, SourceConfig, SourceType


@pytest.fixture
def mock_keyring():
    with patch("data_pipeline.auth.credential_store.keyring") as mock:
        mock.get_password.return_value = None
        mock.errors = MagicMock()
        mock.errors.PasswordDeleteError = Exception
        yield mock


@pytest.fixture
def credential_store(mock_keyring):
    return CredentialStore(service_name="test-pipeline")


@pytest.fixture
def sample_source():
    return SourceConfig(
        id="linear_abc123",
        name="Linear",
        slug="linear",
        source_type=SourceType.API,
        provider="linear",
        oauth=OAuthConfig(
            client_id="test-client",
            authorization_url="https://linear.app/oauth/authorize",
            token_url="https://api.linear.app/oauth/token",
            device_authorization_url="https://linear.app/oauth/authorize/device",
            scopes=["read"],
        ),
    )


class TestCredentialStore:
    def test_store_and_retrieve(self, credential_store, mock_keyring):
        import json

        credential = {"access_token": "tok_123", "token_type": "bearer"}
        credential_store.store("source-1", credential)
        mock_keyring.set_password.assert_called_once()

        stored_value = mock_keyring.set_password.call_args[0][2]
        payload = json.loads(stored_value)
        assert payload["credential"]["access_token"] == "tok_123"

    def test_retrieve_returns_none_when_empty(self, credential_store, mock_keyring):
        mock_keyring.get_password.return_value = None
        result = credential_store.retrieve("nonexistent")
        assert result is None

    def test_retrieve_expired_credential(self, credential_store, mock_keyring):
        import json

        expired_payload = json.dumps({
            "credential": {"access_token": "old"},
            "stored_at": "2020-01-01T00:00:00",
            "expires_at": "2020-01-02T00:00:00",
        })
        mock_keyring.get_password.return_value = expired_payload
        result = credential_store.retrieve("source-1")
        assert result is None

    def test_delete(self, credential_store, mock_keyring):
        result = credential_store.delete("source-1")
        mock_keyring.delete_password.assert_called_once()
        assert result is True


class TestAuthManager:
    def test_is_authenticated_false_when_no_credential(self, mock_keyring):
        manager = AuthManager(credential_store=CredentialStore("test"))
        source = SourceConfig(
            id="test_1",
            name="Test",
            slug="test",
            source_type=SourceType.API,
            provider="test",
        )
        assert not manager.is_authenticated(source)

    def test_store_api_key(self, mock_keyring):
        store = CredentialStore("test")
        manager = AuthManager(credential_store=store)
        source = SourceConfig(
            id="test_1",
            name="Test",
            slug="test",
            source_type=SourceType.API,
            provider="test",
        )
        manager.store_api_key(source, "my-api-key")
        mock_keyring.set_password.assert_called_once()

    def test_authenticate_raises_when_no_credential(self, mock_keyring):
        manager = AuthManager(credential_store=CredentialStore("test"))
        source = SourceConfig(
            id="test_1",
            name="Test",
            slug="test",
            source_type=SourceType.API,
            provider="test",
        )
        with pytest.raises(LookupError, match="No credential found"):
            manager.authenticate(source)
