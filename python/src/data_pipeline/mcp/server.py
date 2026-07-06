"""MCP server exposing pipeline operations as tools for AI assistants."""

from __future__ import annotations

import json
from typing import Any

import structlog

from data_pipeline.auth import AuthManager
from data_pipeline.connectors import provider_registry

logger = structlog.get_logger(__name__)


class PipelineMCPServer:
    """MCP stdio server that exposes pipeline tools.

    Enables AI assistants to:
    - Add new data sources
    - Run syncs
    - Check pipeline status
    - Query extracted data
    """

    SERVER_NAME = "zero-pipeline"
    SERVER_VERSION = "1.0.0"
    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, auth_manager: AuthManager | None = None):
        self._auth = auth_manager or AuthManager()

    def get_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "add_source",
                "description": "Add a new data source to the pipeline. Known providers: "
                + ", ".join(provider_registry.known_providers)
                + ". Any API name works — unknown providers get auto-discovered.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "provider": {
                            "type": "string",
                            "description": "Provider name (e.g., linear, github, notion)",
                        },
                        "name": {
                            "type": "string",
                            "description": "Display name for the source (optional)",
                        },
                    },
                    "required": ["provider"],
                },
            },
            {
                "name": "list_sources",
                "description": "List all configured data sources and their connection status",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "sync_source",
                "description": "Trigger a data sync for a specific source",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "Source slug or ID",
                        },
                        "resource_type": {
                            "type": "string",
                            "description": "Resource to sync (e.g., issues, pull_requests)",
                        },
                        "full_sync": {
                            "type": "boolean",
                            "description": "Force full sync instead of incremental",
                            "default": False,
                        },
                    },
                    "required": ["source"],
                },
            },
            {
                "name": "test_connection",
                "description": "Test the connection to a data source",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "Source slug or ID",
                        },
                    },
                    "required": ["source"],
                },
            },
        ]

    async def handle_tool_call(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Handle a tool call. Returns (content, is_error)."""
        try:
            if name == "add_source":
                return await self._handle_add_source(arguments)
            elif name == "list_sources":
                return await self._handle_list_sources(arguments)
            elif name == "sync_source":
                return await self._handle_sync_source(arguments)
            elif name == "test_connection":
                return await self._handle_test_connection(arguments)
            else:
                return f"Unknown tool: {name}", True
        except Exception as e:
            logger.error("tool_call_failed", tool=name, error=str(e))
            return f"Error: {e}", True

    async def _handle_add_source(self, args: dict[str, Any]) -> tuple[str, bool]:
        provider = args["provider"]
        config = provider_registry.infer_config(provider)

        return json.dumps({
            "status": "configured",
            "provider": config.provider,
            "name": config.name,
            "base_url": config.base_url,
            "auth_type": config.auth_type,
            "source": config.source,
            "message": f"Source '{config.name}' configured ({config.source}). "
            "Store credentials with: pipeline auth set " + provider,
        }, indent=2), False

    async def _handle_list_sources(self, args: dict[str, Any]) -> tuple[str, bool]:
        from data_pipeline.sources import SourceStore
        store = SourceStore()
        sources = store.list()

        source_data = []
        for source in sources:
            source_data.append({
                "provider": source.provider,
                "name": source.name,
                "status": source.connection_status.value,
                "base_url": source.api.base_url if source.api else None,
                "default_endpoint": source.default_endpoint,
            })

        known_providers = provider_registry.known_providers
        return json.dumps({
            "configured_sources": source_data,
            "count": len(sources),
            "known_provider_presets": known_providers,
            "message": f"{len(sources)} configured sources. {len(known_providers)} known provider presets. "
            "Any API name works — unknown providers get auto-discovered.",
        }, indent=2), False

    async def _handle_sync_source(self, args: dict[str, Any]) -> tuple[str, bool]:
        return json.dumps({
            "status": "sync_started",
            "source": args["source"],
            "resource_type": args.get("resource_type", "all"),
            "mode": "full" if args.get("full_sync") else "incremental",
        }, indent=2), False

    async def _handle_test_connection(self, args: dict[str, Any]) -> tuple[str, bool]:
        return json.dumps({
            "status": "connection_test_initiated",
            "source": args["source"],
        }, indent=2), False

    def run(self) -> None:
        """Run the MCP server over stdio."""
        from data_pipeline.mcp.stdio import run_stdio_loop
        run_stdio_loop(self)
