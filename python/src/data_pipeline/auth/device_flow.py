"""OAuth 2.0 Device Authorization Flow (RFC 8628).

This enables browser-based authentication without requiring users to paste tokens.
The user visits a URL and enters a code, similar to `gh auth login`.
"""

from __future__ import annotations

import time

import httpx
import structlog
from rich.console import Console
from rich.panel import Panel

from data_pipeline.schemas import OAuthConfig

logger = structlog.get_logger(__name__)
console = Console()


class DeviceFlowError(Exception):
    pass


class OAuthDeviceFlow:
    """Implements the OAuth 2.0 Device Authorization Grant."""

    def __init__(self, oauth_config: OAuthConfig, *, client_secret: str | None = None):
        self._config = oauth_config
        self._client_secret = client_secret
        self._client = httpx.Client(timeout=30)

    def authorize(self) -> dict:
        """Run the full device authorization flow.

        Returns a token dict with at minimum: access_token, token_type.
        May also include: refresh_token, expires_in, scope.
        """
        device_response = self._request_device_code()
        self._display_user_instructions(device_response)
        return self._poll_for_token(device_response)

    def _request_device_code(self) -> dict:
        url = self._config.device_authorization_url or self._config.authorization_url
        payload = {"client_id": self._config.client_id}
        if self._config.scopes:
            payload["scope"] = " ".join(self._config.scopes)

        response = self._client.post(
            url,
            data=payload,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        data = response.json()

        required = ["device_code", "user_code", "verification_uri"]
        missing = [k for k in required if k not in data]
        if missing:
            raise DeviceFlowError(
                f"Device authorization response missing fields: {missing}"
            )

        return data

    def _display_user_instructions(self, device_response: dict) -> None:
        verification_uri = device_response["verification_uri"]
        user_code = device_response["user_code"]

        console.print()
        console.print(
            Panel(
                f"[bold]Visit:[/bold] [link={verification_uri}]{verification_uri}[/link]\n"
                f"[bold]Enter code:[/bold] [cyan bold]{user_code}[/cyan bold]",
                title="[green]Authenticate in your browser[/green]",
                border_style="green",
            )
        )
        console.print("[dim]Waiting for authorization...[/dim]")

    def _poll_for_token(self, device_response: dict) -> dict:
        device_code = device_response["device_code"]
        interval = device_response.get("interval", 5)
        expires_in = device_response.get("expires_in", 900)
        deadline = time.monotonic() + expires_in

        while time.monotonic() < deadline:
            time.sleep(interval)

            payload = {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": self._config.client_id,
            }
            if self._client_secret:
                payload["client_secret"] = self._client_secret

            response = self._client.post(
                self._config.token_url,
                data=payload,
                headers={"Accept": "application/json"},
            )

            data = response.json()

            if "access_token" in data:
                logger.info("device_flow_authorized")
                console.print("[green bold]Authorized![/green bold]")
                return data

            error = data.get("error")
            if error == "authorization_pending":
                continue
            elif error == "slow_down":
                interval += 5
            elif error == "expired_token":
                raise DeviceFlowError("Device code expired. Please try again.")
            elif error == "access_denied":
                raise DeviceFlowError("Authorization was denied by the user.")
            else:
                raise DeviceFlowError(f"Token request failed: {error}")

        raise DeviceFlowError("Authorization timed out.")

    def refresh_token(self, refresh_token: str) -> dict:
        """Exchange a refresh token for a new access token."""
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._config.client_id,
        }
        if self._client_secret:
            payload["client_secret"] = self._client_secret

        response = self._client.post(
            self._config.token_url,
            data=payload,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()
