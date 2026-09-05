"""Universal API connector with discovery and self-healing."""

from data_pipeline.connectors.base import (
    APIConnector,
    AuthenticationError,
    ConnectorError,
    CursorPagination,
    GraphQLCursorPagination,
    MlflowRunsPagination,
    OffsetPagination,
    PaginationStrategy,
    RateLimitError,
)
from data_pipeline.connectors.discovery import APIDiscovery
from data_pipeline.connectors.llm_discovery import (
    available_providers as available_llm_providers,
)
from data_pipeline.connectors.llm_discovery import (
    discover_auth_docs,
    discover_provider_config,
    get_llm_provider,
    heal_failure,
    plan_action,
    set_llm_provider,
)
from data_pipeline.connectors.registry import ProviderRegistry, provider_registry
from data_pipeline.connectors.self_healing import SelfHealingConnector

__all__ = [
    "APIConnector",
    "APIDiscovery",
    "AuthenticationError",
    "ConnectorError",
    "CursorPagination",
    "GraphQLCursorPagination",
    "MlflowRunsPagination",
    "OffsetPagination",
    "PaginationStrategy",
    "ProviderRegistry",
    "RateLimitError",
    "SelfHealingConnector",
    "available_llm_providers",
    "discover_auth_docs",
    "discover_provider_config",
    "get_llm_provider",
    "heal_failure",
    "plan_action",
    "provider_registry",
    "set_llm_provider",
]
