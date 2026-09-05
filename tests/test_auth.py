"""Tests for authentication module."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from data_pipeline.auth.credential_store import (
    CredentialStore,
    _FileBackend,
    _has_keyring_backend,
)
from data_pipeline.auth.manager import AuthManager
from data_pipeline.schemas import OAuthConfig, SourceConfig, SourceType


def _make_mock_keyring_backend():
    """Create a mock _KeyringBackend with in-memory storage."""
    store = {}
    backend = MagicMock()
    backend.set_password.side_effect = lambda k, v: store.__setitem__(k, v)
    backend.get_password.side_effect = lambda k: store.get(k)

    def _delete(k):
        if k not in store:
            raise Exception("not found")
        del store[k]

    backend.delete_password.side_effect = _delete
    return backend, store


@pytest.fixture
def mock_keyring_store():
    backend, store = _make_mock_keyring_backend()
    with patch(
        "data_pipeline.auth.credential_store._has_keyring_backend",
        return_value=True,
    ), patch(
        "data_pipeline.auth.credential_store._KeyringBackend",
        return_value=backend,
    ):
        cs = CredentialStore(service_name="test-pipeline")
    return cs, backend, store


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
    def test_store_and_retrieve(self, mock_keyring_store):
        cs, backend, store = mock_keyring_store
        credential = {"access_token": "tok_123", "token_type": "bearer"}
        cs.store("source-1", credential)
        backend.set_password.assert_called_once()

        result = cs.retrieve("source-1")
        assert result is not None
        assert result["access_token"] == "tok_123"

    def test_retrieve_returns_none_when_empty(self, mock_keyring_store):
        cs, _, _ = mock_keyring_store
        result = cs.retrieve("nonexistent")
        assert result is None

    def test_retrieve_expired_credential(self, mock_keyring_store):
        import json

        cs, _, store = mock_keyring_store
        expired_payload = json.dumps({
            "credential": {"access_token": "old"},
            "stored_at": "2020-01-01T00:00:00",
            "expires_at": "2020-01-02T00:00:00",
        })
        store["source-1"] = expired_payload
        result = cs.retrieve("source-1")
        assert result is None

    def test_delete(self, mock_keyring_store):
        cs, _, store = mock_keyring_store
        store["source-1"] = '{"credential": {"access_token": "x"}}'
        result = cs.delete("source-1")
        assert result is True

    def test_exists(self, mock_keyring_store):
        cs, _, _ = mock_keyring_store
        assert not cs.exists("nonexistent")
        cs.store("source-1", {"access_token": "tok"})
        assert cs.exists("source-1")


class TestFileBackend:
    def test_store_and_retrieve(self, tmp_path):
        backend = _FileBackend("test-service")
        backend._path = tmp_path / "creds.json"

        backend.set_password("src1", '{"access_token": "tok"}')
        result = backend.get_password("src1")
        assert result is not None
        assert "tok" in result

    def test_retrieve_missing_returns_none(self, tmp_path):
        backend = _FileBackend("test-service")
        backend._path = tmp_path / "creds.json"
        assert backend.get_password("nonexistent") is None

    def test_delete(self, tmp_path):
        backend = _FileBackend("test-service")
        backend._path = tmp_path / "creds.json"

        backend.set_password("src1", "value")
        backend.delete_password("src1")
        assert backend.get_password("src1") is None

    def test_delete_missing_raises(self, tmp_path):
        from data_pipeline.auth.credential_store import _PasswordDeleteError

        backend = _FileBackend("test-service")
        backend._path = tmp_path / "creds.json"

        with pytest.raises(_PasswordDeleteError):
            backend.delete_password("nonexistent")

    def test_file_permissions(self, tmp_path):
        import stat

        backend = _FileBackend("test-service")
        backend._path = tmp_path / "creds.json"

        backend.set_password("src1", "value")

        file_mode = backend._path.stat().st_mode
        assert file_mode & stat.S_IRGRP == 0
        assert file_mode & stat.S_IROTH == 0

    def test_encryption_roundtrip(self, tmp_path):
        backend = _FileBackend("test-service")
        backend._path = tmp_path / "creds.json"

        secret = '{"access_token": "super-secret-key-12345"}'
        backend.set_password("src1", secret)

        raw_content = backend._path.read_text()
        assert "super-secret-key-12345" not in raw_content

        result = backend.get_password("src1")
        assert result == secret


class TestBackendSelection:
    def test_fallback_when_no_keyring(self):
        with patch(
            "data_pipeline.auth.credential_store._has_keyring_backend",
            return_value=False,
        ):
            store = CredentialStore(service_name="test")
            assert store.backend_type == "file"

    def test_keyring_when_available(self):
        with patch(
            "data_pipeline.auth.credential_store._has_keyring_backend",
            return_value=True,
        ):
            store = CredentialStore(service_name="test")
            assert store.backend_type == "keyring"

    @pytest.mark.skipif(sys.platform != "darwin", reason="Only runs on macOS with real keychain")
    def test_has_keyring_backend_true_on_macos(self):
        assert _has_keyring_backend()


class TestCredentialStoreWithFileBackend:
    def test_full_lifecycle(self, tmp_path):
        with patch(
            "data_pipeline.auth.credential_store._has_keyring_backend",
            return_value=False,
        ):
            store = CredentialStore(service_name="test")
            store._backend._path = tmp_path / "creds.json"

            cred = {"access_token": "tok_abc", "token_type": "bearer"}
            store.store("my-source", cred)

            result = store.retrieve("my-source")
            assert result is not None
            assert result["access_token"] == "tok_abc"

            assert store.exists("my-source")
            assert store.delete("my-source")
            assert not store.exists("my-source")


class TestAuthManager:
    def test_is_authenticated_false_when_no_credential(self):
        backend, _ = _make_mock_keyring_backend()
        with patch(
            "data_pipeline.auth.credential_store._has_keyring_backend",
            return_value=True,
        ), patch(
            "data_pipeline.auth.credential_store._KeyringBackend",
            return_value=backend,
        ):
            manager = AuthManager(
                credential_store=CredentialStore("test")
            )
        source = SourceConfig(
            id="test_1",
            name="Test",
            slug="test",
            source_type=SourceType.API,
            provider="test",
        )
        assert not manager.is_authenticated(source)

    def test_store_api_key(self):
        backend, store = _make_mock_keyring_backend()
        with patch(
            "data_pipeline.auth.credential_store._has_keyring_backend",
            return_value=True,
        ), patch(
            "data_pipeline.auth.credential_store._KeyringBackend",
            return_value=backend,
        ):
            cs = CredentialStore("test")
        manager = AuthManager(credential_store=cs)
        source = SourceConfig(
            id="test_1",
            name="Test",
            slug="test",
            source_type=SourceType.API,
            provider="test",
        )
        manager.store_api_key(source, "my-api-key")
        backend.set_password.assert_called_once()

    def test_authenticate_raises_when_no_credential(self):
        backend, _ = _make_mock_keyring_backend()
        with patch(
            "data_pipeline.auth.credential_store._has_keyring_backend",
            return_value=True,
        ), patch(
            "data_pipeline.auth.credential_store._KeyringBackend",
            return_value=backend,
        ):
            manager = AuthManager(
                credential_store=CredentialStore("test")
            )
        source = SourceConfig(
            id="test_1",
            name="Test",
            slug="test",
            source_type=SourceType.API,
            provider="test",
        )
        with pytest.raises(LookupError, match="No credential found"):
            manager.authenticate(source)
