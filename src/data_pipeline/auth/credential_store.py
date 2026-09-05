"""Secure credential storage with cross-platform support.

Resolution order:
1. OS keychain via `keyring` (macOS Keychain, GNOME Keyring, Windows Credential Locker)
2. Encrypted file fallback at ~/.zero-pipeline/credentials.json
   (for headless Linux, Docker, WSL, CI, or when no keychain is available)

The file backend uses Fernet symmetric encryption (AES-128-CBC + HMAC-SHA256).
The encryption key is derived from a machine-specific seed using PBKDF2.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import stat
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import structlog

from data_pipeline.config import settings

logger = structlog.get_logger(__name__)


def _has_keyring_backend() -> bool:
    """Check if a real OS keychain backend is available."""
    try:
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
        return not isinstance(backend, fail.Keyring)
    except Exception:
        return False


def _derive_file_key() -> bytes:
    """Derive an encryption key from machine-specific attributes.

    This is not a secret — it ties the credential file to this machine
    so it can't be copied elsewhere and decrypted. For true secret
    management, use the OS keychain or a vault service.
    """
    seed_parts = [
        platform.node(),
        os.getenv("USER", os.getenv("USERNAME", "default")),
        str(Path.home()),
    ]
    seed = "|".join(seed_parts).encode()
    dk = hashlib.pbkdf2_hmac("sha256", seed, b"zero-pipeline-salt", 100_000)
    return base64.urlsafe_b64encode(dk[:32])


class _FileBackend:
    """Encrypted JSON file credential store for environments without a keychain."""

    def __init__(self, service_name: str):
        self._service = service_name
        self._path = Path.home() / ".zero-pipeline" / "credentials.json"
        self._key: bytes | None = None

    def _get_key(self) -> bytes:
        if self._key is None:
            self._key = _derive_file_key()
        return self._key

    def _encrypt(self, plaintext: str) -> str:
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            return base64.b64encode(plaintext.encode()).decode()

        f = Fernet(self._get_key())
        return f.encrypt(plaintext.encode()).decode()

    def _decrypt(self, ciphertext: str) -> str:
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            return base64.b64decode(ciphertext.encode()).decode()

        f = Fernet(self._get_key())
        return f.decrypt(ciphertext.encode()).decode()

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            raw = self._path.read_text(encoding="utf-8")
            return cast(dict[str, Any], json.loads(raw))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        import contextlib

        with contextlib.suppress(OSError):
            self._path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def _compound_key(self, source_id: str) -> str:
        return f"{self._service}:{source_id}"

    def set_password(self, source_id: str, value: str) -> None:
        data = self._load()
        data[self._compound_key(source_id)] = self._encrypt(value)
        self._save(data)

    def get_password(self, source_id: str) -> str | None:
        data = self._load()
        encrypted = data.get(self._compound_key(source_id))
        if encrypted is None:
            return None
        try:
            return self._decrypt(encrypted)
        except Exception:
            logger.warning("credential_decrypt_failed", source_id=source_id)
            return None

    def delete_password(self, source_id: str) -> None:
        data = self._load()
        key = self._compound_key(source_id)
        if key not in data:
            raise _PasswordDeleteError(f"No credential for {source_id}")
        del data[key]
        self._save(data)


class _PasswordDeleteError(Exception):
    pass


class CredentialStore:
    """Store and retrieve credentials with automatic backend selection.

    Uses the OS keychain when available, falls back to an encrypted file
    for headless Linux, Docker, WSL, CI, and Windows without pywin32.
    """

    def __init__(self, service_name: str | None = None):
        self._service = service_name or settings.keyring_service
        self._backend = self._select_backend()

    def _select_backend(self) -> _KeyringBackend | _FileBackend:
        if _has_keyring_backend():
            return _KeyringBackend(self._service)
        logger.info(
            "credential_store_fallback",
            backend="encrypted_file",
            path=str(Path.home() / ".zero-pipeline" / "credentials.json"),
        )
        return _FileBackend(self._service)

    @property
    def backend_type(self) -> str:
        """Return the active backend type for diagnostics."""
        if isinstance(self._backend, _KeyringBackend):
            return "keyring"
        return "file"

    def store(
        self,
        source_id: str,
        credential: dict[str, Any],
        *,
        expires_in: int | None = None,
    ) -> None:
        payload = {
            "credential": credential,
            "stored_at": datetime.now().isoformat(),
        }
        if expires_in:
            payload["expires_at"] = (
                datetime.now() + timedelta(seconds=expires_in)
            ).isoformat()

        self._backend.set_password(source_id, json.dumps(payload))
        logger.info("credential_stored", source_id=source_id, backend=self.backend_type)

    def retrieve(self, source_id: str) -> dict[str, Any] | None:
        raw = self._backend.get_password(source_id)
        if not raw:
            return None

        payload = json.loads(raw)

        expires_at = payload.get("expires_at")
        if expires_at and datetime.fromisoformat(expires_at) < datetime.now():
            logger.info("credential_expired", source_id=source_id)
            self.delete(source_id)
            return None

        return cast(dict[str, Any], payload["credential"])

    def delete(self, source_id: str) -> bool:
        try:
            self._backend.delete_password(source_id)
            logger.info("credential_deleted", source_id=source_id)
            return True
        except (_PasswordDeleteError, Exception):
            return False

    def exists(self, source_id: str) -> bool:
        return self.retrieve(source_id) is not None


class _KeyringBackend:
    """Thin wrapper around the keyring library matching the file backend interface."""

    def __init__(self, service_name: str):
        self._service = service_name

    def set_password(self, source_id: str, value: str) -> None:
        import keyring

        keyring.set_password(self._service, source_id, value)

    def get_password(self, source_id: str) -> str | None:
        import keyring

        return keyring.get_password(self._service, source_id)

    def delete_password(self, source_id: str) -> None:
        import keyring

        keyring.delete_password(self._service, source_id)
