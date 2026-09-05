"""Unified auth manager coordinating device flow and credential storage."""

from __future__ import annotations

from typing import Any

import structlog

from data_pipeline.auth.credential_store import CredentialStore
from data_pipeline.auth.device_flow import OAuthDeviceFlow
from data_pipeline.schemas import AuthType, SourceConfig

logger = structlog.get_logger(__name__)


class AuthManager:
    """Manages authentication for all source types.

    Coordinates between:
    - OS keyring for secure credential storage
    - OAuth Device Flow for browser-based auth
    - API key prompting as a fallback
    """

    def __init__(self, credential_store: CredentialStore | None = None):
        self._store = credential_store or CredentialStore()

    def authenticate(self, source: SourceConfig) -> dict[str, Any]:
        """Authenticate a source, returning the credential dict.

        For no-auth sources (AuthType.NONE): returns empty dict immediately.
        For OAuth sources: runs device flow if no stored credential exists.
        For API key / basic sources: retrieves from keyring or raises if missing.
        """
        if source.api and source.api.auth_type == AuthType.NONE:
            return {}

        existing = self._store.retrieve(source.id)
        if existing:
            if source.oauth and "refresh_token" in existing:
                return self._maybe_refresh(source, existing)
            return existing

        if source.oauth:
            return self._run_oauth_flow(source)

        raise LookupError(
            f"No credential found for '{source.name}'. "
            f"Store one with: pipeline auth set {source.slug}"
        )

    def store_api_key(self, source: SourceConfig, api_key: str) -> None:
        """Store an API key for a source."""
        credential = {"access_token": api_key, "token_type": "bearer"}
        expires_in = None
        if source.api and source.api.auth_type == AuthType.API_KEY:
            credential["token_type"] = "api_key"

        self._store.store(source.id, credential, expires_in=expires_in)
        logger.info("api_key_stored", source=source.slug)

    def get_auth_header(self, source: SourceConfig) -> dict[str, str]:
        """Get the authorization header for API requests.

        Returns an empty dict for no-auth sources (AuthType.NONE).
        """
        if source.api and source.api.auth_type == AuthType.NONE:
            return {}

        credential = self.authenticate(source)
        token = credential.get("access_token", "")

        if source.api:
            prefix = source.api.auth_prefix
            header_name = source.api.auth_header
            if source.api.auth_type == AuthType.API_KEY and not prefix:
                return {header_name: token}
            return {header_name: f"{prefix} {token}".strip()}

        return {"Authorization": f"Bearer {token}"}

    def is_authenticated(self, source: SourceConfig) -> bool:
        """Check if a source has valid stored credentials.

        No-auth sources (AuthType.NONE) are always considered authenticated.
        """
        if source.api and source.api.auth_type == AuthType.NONE:
            return True
        return self._store.exists(source.id)

    def revoke(self, source: SourceConfig) -> bool:
        """Remove stored credentials for a source."""
        return self._store.delete(source.id)

    def _run_oauth_flow(self, source: SourceConfig) -> dict[str, Any]:
        if not source.oauth:
            raise ValueError(f"Source '{source.name}' has no OAuth configuration")

        flow = OAuthDeviceFlow(source.oauth)
        token_data = flow.authorize()

        expires_in = token_data.get("expires_in")
        self._store.store(source.id, token_data, expires_in=expires_in)
        return token_data

    def _maybe_refresh(self, source: SourceConfig, credential: dict[str, Any]) -> dict[str, Any]:
        """Attempt token refresh if we have a refresh_token."""
        if not source.oauth:
            return credential

        try:
            flow = OAuthDeviceFlow(source.oauth)
            new_tokens = flow.refresh_token(credential["refresh_token"])
            expires_in = new_tokens.get("expires_in")
            self._store.store(source.id, new_tokens, expires_in=expires_in)
            logger.info("token_refreshed", source=source.slug)
            return new_tokens
        except Exception:
            logger.warning("token_refresh_failed", source=source.slug)
            return self._run_oauth_flow(source)
