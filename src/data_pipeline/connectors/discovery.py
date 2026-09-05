"""LLM-driven API discovery for all providers.

Discovery runs for EVERY provider — preset or not:

1. Preset providers (mlflow, wandb, airflow, etc.):
   - Start from the preset config (base URL, auth style, pagination)
   - LLM enriches with deeper endpoint knowledge, rate limits, quirks
   - Result is merged: preset values are authoritative, LLM fills gaps

2. Unknown providers (your-feature-store, internal-api, etc.):
   - LLM discovers base URL, auth type, pagination style, endpoints
   - Falls back to name-based inference if LLM unavailable
   - HTTP probing validates reachability

This means even "known" providers benefit from LLM intelligence —
the LLM can discover new endpoints, current rate limits, and API quirks
that the static preset doesn't capture.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from data_pipeline.connectors.base import APIConnector
from data_pipeline.connectors.registry import provider_registry

logger = structlog.get_logger(__name__)


class APIDiscovery:
    """Discovers API capabilities for any provider.

    Strategy for ALL providers (preset or not):
    1. Get base config (preset if known, name-inference if not)
    2. Run LLM discovery to enrich — endpoints, rate limits, quirks
    3. HTTP probe to validate reachability and infer pagination hints
    4. Merge: explicit user overrides > preset > LLM > probe > inference
    """

    def __init__(self, provider: str, *, base_url: str | None = None):
        self._provider = provider.lower()
        self._base_url = base_url

    async def discover(self) -> dict[str, Any]:
        """Discover API configuration for a provider.

        Returns a dict with: base_url, auth_type, pagination_style,
        default_endpoints, schema hints, source, etc.

        Runs LLM enrichment for ALL providers — preset or not.
        """
        inferred = provider_registry.infer_config(self._provider)
        config = inferred.model_dump()

        # User-supplied base_url always wins
        if self._base_url:
            config["base_url"] = self._base_url

        # --- LLM enrichment (runs for ALL providers) ---
        from data_pipeline.connectors.llm_discovery import discover_provider_config

        llm_config = discover_provider_config(self._provider)
        if llm_config:
            source_tag = "preset+llm" if inferred.source == "preset" else "llm_discovered"
            logger.info("discovery_llm_enriched", provider=self._provider, source=source_tag)

            for key, value in llm_config.items():
                if value is None:
                    continue
                # User override and preset base_url are authoritative
                if key == "base_url" and self._base_url:
                    continue
                # For presets, only fill in MISSING fields (don't overwrite)
                if inferred.source == "preset" and key in config and config[key]:
                    # Extend endpoints dict rather than replace
                    if key == "default_endpoints" and isinstance(value, dict):
                        existing = config.get("default_endpoints") or {}
                        config["default_endpoints"] = {**value, **existing}
                    continue
                config[key] = value

            config["source"] = source_tag
        else:
            if inferred.source == "preset":
                config["source"] = "preset"
            else:
                # No LLM — fall back to HTTP probing
                logger.info(
                    "discovery_probing",
                    provider=self._provider,
                    base_url=config["base_url"],
                )
                probed = await self._probe_api(config["base_url"])
                config.update(probed)
                config["source"] = "probed"

        return config

    async def discover_endpoints(self, connector: APIConnector) -> list[dict[str, Any]]:
        """Discover available endpoints by probing the API for an OpenAPI spec."""
        endpoints: list[dict[str, Any]] = []
        openapi_paths = [
            "/openapi.json",
            "/swagger.json",
            "/api/v1/openapi",
            "/.well-known/openapi",
            "/api-docs",
        ]
        async with connector:
            for path in openapi_paths:
                try:
                    response = await connector.request("GET", path)
                    spec = response.json()
                    if "paths" in spec:
                        for route, methods in spec["paths"].items():
                            for method in methods:
                                if method.upper() in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
                                    endpoints.append({
                                        "path": route,
                                        "method": method.upper(),
                                        "summary": methods[method].get("summary", ""),
                                    })
                        logger.info(
                            "openapi_discovered",
                            provider=self._provider,
                            endpoints=len(endpoints),
                        )
                        return endpoints
                except Exception:
                    continue
        return endpoints

    async def infer_schema(self, connector: APIConnector, path: str) -> dict[str, Any]:
        """Infer the schema of an endpoint from a sample response."""
        async with connector:
            try:
                response = await connector.request(
                    "GET", path, params={"per_page": 1, "limit": 1}
                )
                return self._schema_from_sample(response.json())
            except Exception as e:
                logger.warning("schema_inference_failed", path=path, error=str(e))
                return {"fields": [], "error": str(e)}

    async def _probe_api(self, base_url: str) -> dict[str, Any]:
        """HTTP probe to validate reachability and detect pagination hints."""
        result: dict[str, Any] = {}
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                response = await client.get(base_url, follow_redirects=True)
                result["reachable"] = True
                result["status_code"] = response.status_code

                if "x-ratelimit-limit" in response.headers:
                    result["rate_limit_rpm"] = int(
                        response.headers.get("x-ratelimit-limit", "60")
                    )
                if "link" in response.headers:
                    result["pagination_style"] = "link_header"

                content_type = response.headers.get("content-type", "")
                if "json" in content_type:
                    data = response.json()
                    if isinstance(data, dict) and any(k in data for k in ("next_cursor", "next_page_token", "has_more")):
                        result["pagination_style"] = "cursor"

            except httpx.ConnectError:
                result["reachable"] = False
                result["error"] = "Connection failed"
            except Exception as e:
                result["reachable"] = True
                result["error"] = str(e)

        return result

    def _schema_from_sample(self, data: Any) -> dict[str, Any]:
        """Infer schema from a sample API response."""
        if isinstance(data, list) and data:
            sample = data[0]
        elif isinstance(data, dict):
            for key in ("results", "data", "items", "nodes", "runs", "experiments"):
                if key in data and isinstance(data[key], list) and data[key]:
                    sample = data[key][0]
                    break
            else:
                sample = data
        else:
            return {"fields": []}

        if not isinstance(sample, dict):
            return {"fields": []}

        return {
            "fields": [
                {
                    "name": k,
                    "type": (
                        "nullable" if v is None
                        else "object" if isinstance(v, dict)
                        else "array" if isinstance(v, list)
                        else type(v).__name__
                    ),
                }
                for k, v in sample.items()
            ],
            "sample_keys": list(sample.keys()),
        }
