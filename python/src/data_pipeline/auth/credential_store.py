"""Secure credential storage using OS keyring."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import keyring
import structlog

from data_pipeline.config import settings

logger = structlog.get_logger(__name__)


class CredentialStore:
    """Store and retrieve credentials from the OS keychain."""

    def __init__(self, service_name: str | None = None):
        self._service = service_name or settings.keyring_service

    def store(
        self,
        source_id: str,
        credential: dict,
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

        keyring.set_password(self._service, source_id, json.dumps(payload))
        logger.info("credential_stored", source_id=source_id)

    def retrieve(self, source_id: str) -> dict | None:
        raw = keyring.get_password(self._service, source_id)
        if not raw:
            return None

        payload = json.loads(raw)

        expires_at = payload.get("expires_at")
        if expires_at and datetime.fromisoformat(expires_at) < datetime.now():
            logger.info("credential_expired", source_id=source_id)
            self.delete(source_id)
            return None

        return payload["credential"]

    def delete(self, source_id: str) -> bool:
        try:
            keyring.delete_password(self._service, source_id)
            logger.info("credential_deleted", source_id=source_id)
            return True
        except keyring.errors.PasswordDeleteError:
            return False

    def exists(self, source_id: str) -> bool:
        return self.retrieve(source_id) is not None
