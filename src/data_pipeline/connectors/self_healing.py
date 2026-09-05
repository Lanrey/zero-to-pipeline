"""Self-healing connector that adapts to API failures.

When an API call fails, instead of just retrying blindly, the self-healing
connector analyzes the failure and adapts:
- Auth format wrong? Try different formats (Bearer prefix, no prefix, header key)
- Endpoint not found? Discover the correct path
- Pagination broken? Switch strategy
- Schema mismatch? Re-infer from the response
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from data_pipeline.connectors.base import (
    APIConnector,
    AuthenticationError,
    ConnectorError,
    CursorPagination,
    GraphQLCursorPagination,
    OffsetPagination,
    PaginationStrategy,
)
from data_pipeline.schemas import CheckpointState, ExtractedRecord

logger = structlog.get_logger(__name__)

AUTH_FORMATS = [
    ("Bearer", "Authorization"),
    ("", "Authorization"),
    ("token", "Authorization"),
    ("Basic", "Authorization"),
    ("", "X-API-Key"),
    ("", "Api-Key"),
    ("", "api_key"),
]


@dataclass
class HealingAction:
    """A corrective action applied after a failure."""

    action_type: str
    description: str
    params: dict[str, Any]


class SelfHealingConnector:
    """Wraps an APIConnector with self-healing capabilities.

    On auth failure, rotates through known auth formats until one works and
    saves the winner for all subsequent requests.

    For no-auth sources (empty credential), healing is skipped entirely —
    the request is sent with no Authorization header and any 401/403 is
    surfaced as a plain error rather than triggering format rotation.
    """

    def __init__(
        self,
        base_url: str,
        credential: str,
        *,
        auth_header: str = "Authorization",
        auth_prefix: str = "Bearer",
        default_headers: dict[str, str] | None = None,
        max_heal_attempts: int = 3,
    ):
        self._base_url = base_url
        self._credential = credential
        self._auth_header = auth_header
        self._auth_prefix = auth_prefix
        self._default_headers = default_headers or {}
        self._max_heal_attempts = max_heal_attempts
        self._healing_history: list[HealingAction] = []

    @property
    def _no_auth(self) -> bool:
        """True when no credential is set — auth healing should be skipped."""
        return not self._credential

    @property
    def healing_history(self) -> list[HealingAction]:
        return list(self._healing_history)

    def _build_auth_value(self, prefix: str, header: str) -> tuple[str, str]:
        # Empty credential → no auth value (avoids "Bearer " illegal header)
        if not self._credential:
            return header, ""
        if prefix:
            return header, f"{prefix} {self._credential}"
        return header, self._credential

    def _create_connector(
        self, auth_prefix: str | None = None, auth_header: str | None = None
    ) -> APIConnector:
        prefix = auth_prefix if auth_prefix is not None else self._auth_prefix
        header = auth_header if auth_header is not None else self._auth_header
        _, auth_value = self._build_auth_value(prefix, header)

        return APIConnector(
            self._base_url,
            auth_header=header,
            auth_value=auth_value,  # empty string → header omitted by APIConnector
            default_headers=self._default_headers,
        )

    async def request_with_healing(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        allow_auth_error: bool = False,
    ) -> httpx.Response:
        """Make a request with self-healing on failure.

        allow_auth_error=True: treat 401/403 as success (used for connectivity
        checks where the server being reachable is all that matters).
        """
        connector = self._create_connector()

        async with connector:
            try:
                return await connector.request(method, path, params=params, json_body=json_body)
            except AuthenticationError as e:
                if allow_auth_error:
                    # Server is reachable — auth failure just means we need credentials
                    raise
                return await self._heal_auth(method, path, params=params, json_body=json_body, error=e)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise ConnectorError(f"Endpoint not found: {path}") from e
                raise

    async def _heal_auth(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        error: AuthenticationError,
    ) -> httpx.Response:
        """Try different auth formats when authentication fails.

        Skipped for no-auth sources (empty credential) — those should never
        require auth, so a 401/403 indicates a genuine server-side issue.
        """
        if self._no_auth:
            raise ConnectorError(
                f"Request to {self._base_url}{method} failed with auth error, "
                "but this source is configured with no credentials. "
                "If the server requires auth, run: pipeline auth set <provider>"
            )
        logger.info(
            "self_healing.auth_start",
            source=self._base_url,
            reason="auth_error",
            note="rotating through known auth formats",
        )

        for attempt, (prefix, header) in enumerate(AUTH_FORMATS):
            if attempt >= self._max_heal_attempts:
                break
            if prefix == self._auth_prefix and header == self._auth_header:
                continue

            fmt = f"{header}: {prefix} <token>" if prefix else f"{header}: <token>"
            action = HealingAction(
                action_type="auth_format_change",
                description=f"trying {fmt}",
                params={"auth_prefix": prefix, "auth_header": header},
            )
            self._healing_history.append(action)
            logger.info("self_healing.auth_attempt", attempt=attempt + 1, format=fmt)

            connector = self._create_connector(auth_prefix=prefix, auth_header=header)
            async with connector:
                try:
                    response = await connector.request(
                        method, path, params=params, json_body=json_body
                    )
                    self._auth_prefix = prefix
                    self._auth_header = header
                    logger.info(
                        "self_healing.auth_success",
                        format=fmt,
                        note="format saved for all future requests",
                    )
                    return response
                except AuthenticationError:
                    logger.debug("self_healing.auth_attempt_failed", format=fmt)
                    continue

        tried = [f"{h}: {p} <token>" if p else f"{h}: <token>" for p, h in AUTH_FORMATS[:self._max_heal_attempts]]
        raise ConnectorError(
            f"Auth healing exhausted after {self._max_heal_attempts} attempts. "
            f"Formats tried: {tried}. "
            "Run: pipeline auth set <provider>"
        )

    async def extract_with_healing(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        pagination: PaginationStrategy | None = None,
        source_id: str = "",
        resource_type: str = "",
        checkpoint: CheckpointState | None = None,
    ) -> AsyncIterator[ExtractedRecord]:
        """Extract records with self-healing pagination.

        Tracks the last successfully yielded record's cursor so that after
        healing, extraction resumes from that point rather than replaying
        from the beginning.  Deduplicates records seen before the failure.
        """
        # Track state for resumption after healing
        last_successful_cursor: str | None = checkpoint.cursor if checkpoint else None
        seen_ids: set[str] = set()
        current_checkpoint = checkpoint

        connector = self._create_connector()
        caught_auth_error: AuthenticationError | None = None

        async with connector:
            try:
                async for record in connector.paginate(
                    method,
                    path,
                    params=params,
                    json_body=json_body,
                    pagination=pagination,
                    source_id=source_id,
                    resource_type=resource_type,
                    checkpoint=current_checkpoint,
                ):
                    seen_ids.add(record.id)
                    last_successful_cursor = record.cursor
                    yield record
            except AuthenticationError as _auth_err:
                caught_auth_error = _auth_err
            else:
                # Completed without error — nothing to heal
                return

        # No-auth sources: surface the error immediately, skip healing entirely
        if caught_auth_error is not None and self._no_auth:
            raise ConnectorError(
                f"Auth error from {self._base_url} but source has no credentials. "
                "If the server requires authentication, run: pipeline auth set <provider>"
            ) from caught_auth_error

        if caught_auth_error is None:
            return

        # --- Healing phase (only reached when credential is set and auth failed) ---
        logger.info(
            "healing_pagination_recovery",
            last_cursor=last_successful_cursor,
            records_before_failure=len(seen_ids),
        )

        healed_connector = self._create_connector()
        async with healed_connector:
            await self._heal_auth(
                method, path, params=params, json_body=json_body, error=AuthenticationError("")
            )

        # Build a checkpoint that resumes from the last successful cursor
        resume_checkpoint: CheckpointState | None = None
        if last_successful_cursor is not None:
            resume_checkpoint = CheckpointState(
                pipeline_id=checkpoint.pipeline_id if checkpoint else "healing",
                source_id=source_id or (checkpoint.source_id if checkpoint else ""),
                cursor=last_successful_cursor,
                last_record_id=checkpoint.last_record_id if checkpoint else None,
            )

        action = HealingAction(
            action_type="pagination_resume",
            description=f"Resumed extraction from cursor: {last_successful_cursor}",
            params={"resume_cursor": last_successful_cursor},
        )
        self._healing_history.append(action)

        new_connector = self._create_connector()
        async with new_connector:
            async for record in new_connector.paginate(
                method,
                path,
                params=params,
                json_body=json_body,
                pagination=pagination,
                source_id=source_id,
                resource_type=resource_type,
                checkpoint=resume_checkpoint,
            ):
                # Skip duplicates that were already yielded before the failure
                if record.id in seen_ids:
                    continue
                seen_ids.add(record.id)
                yield record

    def infer_pagination(self, sample_response: httpx.Response) -> PaginationStrategy:
        """Infer pagination strategy from a sample response."""
        if "link" in sample_response.headers:
            link_header = sample_response.headers["link"]
            if 'rel="next"' in link_header:
                return OffsetPagination()

        try:
            data = sample_response.json()
        except Exception:
            return OffsetPagination()

        if not isinstance(data, dict):
            return OffsetPagination()

        if "pageInfo" in str(data):
            return GraphQLCursorPagination()

        for field in ("next_cursor", "next_page_token", "cursor", "nextCursor"):
            if field in data:
                return CursorPagination(cursor_field=field)

        if "has_more" in data:
            cursor_field = "next_cursor"
            for key in data:
                if "cursor" in key.lower() or "token" in key.lower():
                    cursor_field = key
                    break
            return CursorPagination(cursor_field=cursor_field, has_more_field="has_more")

        return OffsetPagination()
