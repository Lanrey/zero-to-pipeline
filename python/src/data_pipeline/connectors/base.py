"""Base connector with retry, pagination, and rate limiting built in."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from data_pipeline.config import settings
from data_pipeline.schemas import (
    CheckpointState,
    ExtractedRecord,
)

logger = structlog.get_logger(__name__)


class RateLimitError(Exception):
    """Raised when API rate limit is hit."""

    def __init__(self, retry_after: float = 60.0):
        self.retry_after = retry_after
        super().__init__(f"Rate limited. Retry after {retry_after}s")


class ConnectorError(Exception):
    """Base error for connector failures."""
    pass


class AuthenticationError(ConnectorError):
    """Raised when authentication fails (401/403)."""
    pass


class APIConnector:
    """Universal API connector with automatic retry, pagination, and rate limiting.

    This is NOT a per-provider hardcoded connector. It connects to ANY REST or
    GraphQL API given a base URL and auth configuration. The intelligence for
    understanding specific APIs comes from:
    - LLM-driven API discovery (what endpoints exist)
    - Schema inference from response payloads
    - Self-healing on failure (auto-adjusting params, auth format, etc.)
    """

    def __init__(
        self,
        base_url: str,
        *,
        auth_header: str = "Authorization",
        auth_value: str = "",
        default_headers: dict[str, str] | None = None,
        timeout: int | None = None,
        max_retries: int | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._auth_header = auth_header
        self._auth_value = auth_value
        self._default_headers = default_headers or {}
        self._timeout = timeout or settings.default_timeout
        self._max_retries = max_retries or settings.max_retries
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> APIConnector:
        headers = {**self._default_headers}
        if self._auth_value:
            headers[self._auth_header] = self._auth_value
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=self._timeout,
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, RateLimitError)),
        stop=stop_after_attempt(settings.max_retries),
        wait=wait_exponential(
            multiplier=settings.retry_base_delay,
            max=settings.retry_max_delay,
        ),
        reraise=True,
    )
    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Make an authenticated HTTP request with automatic retry and rate limit handling."""
        assert self._client is not None, "Connector not initialized. Use async with."

        response = await self._client.request(
            method, path, params=params, json=json_body, headers=headers
        )

        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", "60"))
            logger.warning("rate_limited", url=path, retry_after=retry_after)
            await asyncio.sleep(retry_after)
            raise RateLimitError(retry_after)

        if response.status_code in {401, 403}:
            raise AuthenticationError(
                f"Authentication failed ({response.status_code}): {response.text[:200]}"
            )

        response.raise_for_status()
        return response

    async def paginate(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        pagination: PaginationStrategy | None = None,
        checkpoint: CheckpointState | None = None,
        source_id: str = "",
        resource_type: str = "",
    ) -> AsyncIterator[ExtractedRecord]:
        """Generic paginated request that yields ExtractedRecords.

        Supports multiple pagination strategies:
        - cursor: Uses a cursor/next_token field in the response
        - offset: Uses offset/limit parameters
        - link_header: Follows Link: <url>; rel="next" headers
        - graphql_cursor: Uses pageInfo.endCursor in GraphQL responses
        """
        strategy = pagination or OffsetPagination()
        state = strategy.init_state(checkpoint)

        while True:
            req_params, req_body = strategy.apply_to_request(
                params or {}, json_body, state
            )

            response = await self.request(
                method, path, params=req_params, json_body=req_body
            )
            data = response.json()

            records = strategy.extract_records(data)
            if not records:
                break

            for record_data in records:
                record_id = str(
                    record_data.get("id")
                    or record_data.get("_id")
                    or hash(str(record_data))
                )
                yield ExtractedRecord(
                    id=record_id,
                    source_id=source_id,
                    resource_type=resource_type,
                    raw_data=record_data,
                    cursor=strategy.get_cursor(data, state),
                )

            next_state = strategy.next_page(data, response, state)
            if next_state is None:
                break
            state = next_state

    async def test_connection(self, health_path: str = "/") -> bool:
        """Verify connectivity to the API."""
        try:
            async with self:
                await self.request("GET", health_path)
                return True
        except Exception as e:
            logger.error("connection_test_failed", base_url=self.base_url, error=str(e))
            return False


class PaginationStrategy:
    """Base class for pagination strategies."""

    def init_state(self, checkpoint: CheckpointState | None) -> dict[str, Any]:
        return {"cursor": checkpoint.cursor if checkpoint else None}

    def apply_to_request(
        self,
        params: dict[str, Any],
        json_body: dict[str, Any] | None,
        state: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        return params, json_body

    def extract_records(self, data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("results", "data", "items", "nodes", "records", "entries"):
                if key in data and isinstance(data[key], list):
                    return data[key]
        return []

    def get_cursor(self, data: Any, state: dict[str, Any]) -> str | None:
        return state.get("cursor")

    def next_page(
        self,
        data: Any,
        response: httpx.Response,
        state: dict[str, Any],
    ) -> dict[str, Any] | None:
        return None


class OffsetPagination(PaginationStrategy):
    """Offset/limit pagination (page=N or offset=N)."""

    def __init__(self, page_size: int = 100, param_name: str = "page"):
        self._page_size = page_size
        self._param_name = param_name

    def init_state(self, checkpoint: CheckpointState | None) -> dict[str, Any]:
        page = 1
        if checkpoint and checkpoint.metadata.get("page"):
            page = int(checkpoint.metadata["page"])
        return {"page": page, "cursor": str(page)}

    def apply_to_request(self, params, json_body, state):
        params = {**params, self._param_name: state["page"], "per_page": self._page_size}
        return params, json_body

    def get_cursor(self, data, state):
        return str(state["page"])

    def next_page(self, data, response, state):
        records = self.extract_records(data)
        if len(records) < self._page_size:
            return None

        link = response.headers.get("Link", "")
        if link and 'rel="next"' not in link:
            return None

        return {"page": state["page"] + 1, "cursor": str(state["page"] + 1)}


class CursorPagination(PaginationStrategy):
    """Cursor-based pagination (next_cursor, start_cursor, after, etc.)."""

    def __init__(
        self,
        cursor_field: str = "next_cursor",
        cursor_param: str = "cursor",
        has_more_field: str = "has_more",
        page_size: int = 100,
        in_body: bool = False,
    ):
        self._cursor_field = cursor_field
        self._cursor_param = cursor_param
        self._has_more_field = has_more_field
        self._page_size = page_size
        self._in_body = in_body

    def apply_to_request(self, params, json_body, state):
        cursor = state.get("cursor")
        if self._in_body:
            body = json_body or {}
            body["page_size"] = self._page_size
            if cursor:
                body[self._cursor_param] = cursor
            return params, body
        else:
            params = {**params, "limit": self._page_size}
            if cursor:
                params[self._cursor_param] = cursor
            return params, json_body

    def get_cursor(self, data, state):
        if isinstance(data, dict):
            return data.get(self._cursor_field) or state.get("cursor")
        return state.get("cursor")

    def next_page(self, data, response, state):
        if not isinstance(data, dict):
            return None
        has_more = data.get(self._has_more_field, False)
        next_cursor = data.get(self._cursor_field)
        if not has_more or not next_cursor:
            return None
        return {"cursor": next_cursor}


class GraphQLCursorPagination(PaginationStrategy):
    """GraphQL relay-style cursor pagination with pageInfo."""

    def __init__(self, page_size: int = 50, data_path: str | None = None):
        self._page_size = page_size
        self._data_path = data_path

    def apply_to_request(self, params, json_body, state):
        body = json_body or {}
        variables = body.get("variables", {})
        variables["first"] = self._page_size
        cursor = state.get("cursor")
        if cursor:
            variables["after"] = cursor
        body["variables"] = variables
        return params, body

    def extract_records(self, data):
        if not isinstance(data, dict):
            return []
        gql_data = data.get("data", data)
        if isinstance(gql_data, dict):
            for value in gql_data.values():
                if isinstance(value, dict) and "nodes" in value:
                    return value["nodes"]
                if isinstance(value, dict) and "edges" in value:
                    return [edge["node"] for edge in value["edges"]]
        return []

    def get_cursor(self, data, state):
        page_info = self._get_page_info(data)
        return page_info.get("endCursor") if page_info else state.get("cursor")

    def next_page(self, data, response, state):
        page_info = self._get_page_info(data)
        if not page_info or not page_info.get("hasNextPage"):
            return None
        return {"cursor": page_info["endCursor"]}

    def _get_page_info(self, data: Any) -> dict | None:
        if not isinstance(data, dict):
            return None
        gql_data = data.get("data", data)
        if isinstance(gql_data, dict):
            for value in gql_data.values():
                if isinstance(value, dict) and "pageInfo" in value:
                    return value["pageInfo"]
        return None
