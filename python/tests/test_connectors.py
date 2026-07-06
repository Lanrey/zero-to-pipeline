"""Tests for the universal connector, registry, and self-healing."""

from __future__ import annotations

import pytest
import httpx
import respx

from data_pipeline.connectors import (
    APIConnector,
    APIDiscovery,
    CursorPagination,
    GraphQLCursorPagination,
    OffsetPagination,
    SelfHealingConnector,
    provider_registry,
)
from data_pipeline.connectors.base import AuthenticationError, RateLimitError


class TestProviderRegistry:
    def test_known_providers_exist(self):
        providers = provider_registry.known_providers
        assert "linear" in providers
        assert "github" in providers
        assert "notion" in providers

    def test_has_preset(self):
        assert provider_registry.has_preset("linear")
        assert provider_registry.has_preset("github")
        assert not provider_registry.has_preset("some-random-api-xyz")

    def test_infer_known_provider(self):
        config = provider_registry.infer_config("linear")
        assert config.base_url == "https://api.linear.app"
        assert config.source == "preset"
        assert config.pagination_style == "graphql_cursor"
        assert config.default_endpoints == {"issues": "/graphql", "projects": "/graphql"}

    def test_infer_unknown_provider(self):
        config = provider_registry.infer_config("acme-corp")
        assert config.base_url == "https://api.acme-corp.com"
        assert config.source == "inferred"
        assert config.auth_type == "bearer"

    def test_infer_preserves_provider_name(self):
        config = provider_registry.infer_config("MyCustomAPI")
        assert config.provider == "mycustomapi"
        assert config.name == "Mycustomapi"

    def test_infer_returns_typed_model(self):
        from data_pipeline.schemas import InferredConfig
        config = provider_registry.infer_config("github")
        assert isinstance(config, InferredConfig)
        assert config.default_endpoints["issues"] == "/repos/{owner}/{repo}/issues"

    def test_preset_details(self):
        preset = provider_registry.get_preset("github")
        assert preset is not None
        assert preset.rate_limit_rpm == 5000
        assert "Accept" in preset.default_headers


class TestAPIConnector:
    @pytest.mark.asyncio
    @respx.mock
    async def test_basic_request(self):
        respx.get("https://api.example.com/items").mock(
            return_value=httpx.Response(200, json=[{"id": "1", "name": "test"}])
        )

        connector = APIConnector("https://api.example.com", auth_value="Bearer tok123")
        async with connector:
            response = await connector.request("GET", "/items")
            assert response.status_code == 200
            assert response.json()[0]["name"] == "test"

    @pytest.mark.asyncio
    @respx.mock
    async def test_auth_error_raises(self):
        respx.get("https://api.example.com/secret").mock(
            return_value=httpx.Response(401, json={"error": "unauthorized"})
        )

        connector = APIConnector("https://api.example.com", auth_value="Bearer bad")
        async with connector:
            with pytest.raises(AuthenticationError):
                await connector.request("GET", "/secret")

    @pytest.mark.asyncio
    @respx.mock
    async def test_paginate_offset(self):
        respx.get("https://api.example.com/items").mock(
            side_effect=[
                httpx.Response(200, json=[{"id": "1"}, {"id": "2"}]),
                httpx.Response(200, json=[]),
            ]
        )

        connector = APIConnector("https://api.example.com", auth_value="tok")
        records = []
        async with connector:
            async for record in connector.paginate(
                "GET",
                "/items",
                pagination=OffsetPagination(page_size=10),
                source_id="test",
                resource_type="items",
            ):
                records.append(record)

        assert len(records) == 2
        assert records[0].id == "1"

    @pytest.mark.asyncio
    @respx.mock
    async def test_paginate_cursor(self):
        respx.get("https://api.example.com/data").mock(
            side_effect=[
                httpx.Response(200, json={
                    "results": [{"id": "a"}],
                    "has_more": True,
                    "next_cursor": "cur_2",
                }),
                httpx.Response(200, json={
                    "results": [{"id": "b"}],
                    "has_more": False,
                    "next_cursor": None,
                }),
            ]
        )

        connector = APIConnector("https://api.example.com", auth_value="tok")
        records = []
        async with connector:
            async for record in connector.paginate(
                "GET",
                "/data",
                pagination=CursorPagination(
                    cursor_field="next_cursor",
                    has_more_field="has_more",
                ),
                source_id="test",
                resource_type="data",
            ):
                records.append(record)

        assert len(records) == 2


class TestSelfHealingConnector:
    @pytest.mark.asyncio
    @respx.mock
    async def test_heals_auth_format(self):
        call_count = {"n": 0}

        def handler(request):
            call_count["n"] += 1
            auth = request.headers.get("Authorization", "")
            if auth == "Bearer tok123":
                return httpx.Response(401, json={"error": "unauthorized"})
            if auth == "tok123":
                return httpx.Response(200, json={"status": "ok"})
            return httpx.Response(401)

        respx.get("https://api.example.com/test").mock(side_effect=handler)

        connector = SelfHealingConnector(
            "https://api.example.com",
            "tok123",
            auth_prefix="Bearer",
        )
        response = await connector.request_with_healing("GET", "/test")
        assert response.status_code == 200

        assert len(connector.healing_history) > 0
        assert connector._auth_prefix == ""

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_healing_when_auth_works(self):
        respx.get("https://api.example.com/ok").mock(
            return_value=httpx.Response(200, json={"data": "success"})
        )

        connector = SelfHealingConnector(
            "https://api.example.com",
            "tok123",
            auth_prefix="Bearer",
        )
        response = await connector.request_with_healing("GET", "/ok")
        assert response.status_code == 200
        assert len(connector.healing_history) == 0


class TestPaginationStrategies:
    def test_offset_extract_records_list(self):
        strategy = OffsetPagination()
        assert strategy.extract_records([{"id": 1}, {"id": 2}]) == [{"id": 1}, {"id": 2}]

    def test_offset_extract_records_nested(self):
        strategy = OffsetPagination()
        data = {"results": [{"id": 1}], "total": 1}
        assert strategy.extract_records(data) == [{"id": 1}]

    def test_graphql_extract_nodes(self):
        strategy = GraphQLCursorPagination()
        data = {"data": {"issues": {"nodes": [{"id": "a"}], "pageInfo": {"hasNextPage": False}}}}
        assert strategy.extract_records(data) == [{"id": "a"}]

    def test_graphql_next_page(self):
        strategy = GraphQLCursorPagination()
        data = {"data": {"items": {"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": "x"}}}}

        response = httpx.Response(200)
        result = strategy.next_page(data, response, {})
        assert result == {"cursor": "x"}

    def test_graphql_no_next_page(self):
        strategy = GraphQLCursorPagination()
        data = {"data": {"items": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": "x"}}}}

        response = httpx.Response(200)
        result = strategy.next_page(data, response, {})
        assert result is None


class TestAPIDiscovery:
    def test_discover_known_provider(self):
        discovery = APIDiscovery("linear")

        async def _run():
            return await discovery.discover()

        import asyncio
        config = asyncio.run(_run())
        assert config["base_url"] == "https://api.linear.app"
        assert config["source"] == "preset"

    def test_discover_unknown_provider_infers_url(self):
        discovery = APIDiscovery("acme-widgets")

        async def _run():
            return await discovery.discover()

        import asyncio
        config = asyncio.run(_run())
        assert "acme-widgets" in config["base_url"]
