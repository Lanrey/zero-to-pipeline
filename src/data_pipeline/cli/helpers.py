"""Shared CLI utilities and helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import typer
from rich.console import Console

from data_pipeline.auth import CredentialStore
from data_pipeline.connectors import provider_registry
from data_pipeline.schemas import APIConfig, AuthType
from data_pipeline.sources import SourceStore

console = Console()

# GraphQL default queries
DEFAULT_GRAPHQL_INTROSPECTION = """
query { __schema { queryType { fields { name description } } } }
"""

GRAPHQL_ISSUES_QUERY = """
query($first: Int) {
  issues(first: $first) {
    nodes { id title state createdAt updatedAt }
    pageInfo { hasNextPage endCursor }
  }
}
"""


@dataclass
class ResolvedConnection:
    """Resolved provider connection — config merged with persisted source and credential."""

    url: str
    auth_header: str
    auth_prefix: str
    token: str
    no_auth: bool
    default_headers: dict[str, str] = field(default_factory=dict)
    health_endpoint: str = "/"


def resolve_for_provider(
    provider: str,
    *,
    base_url: str | None = None,
) -> ResolvedConnection:
    """Resolve a provider name into a full connection config.

    Merges three layers:
    1. Registry preset (or name-based inference)
    2. Persisted source config (user overrides)
    3. Credential store (auth token)

    Calls ``show_auth_docs`` and exits if credentials are required but missing.
    """
    config = provider_registry.infer_config(provider)
    persisted = SourceStore().get(provider.lower())

    if persisted and persisted.api:
        p = persisted.api
        url = base_url or p.base_url
        no_auth = p.auth_type == AuthType.NONE
        auth_header = p.auth_header
        auth_prefix = p.auth_prefix
        default_headers = p.default_headers
        health = config.health_endpoint
    else:
        url = base_url or config.base_url
        no_auth = config.auth_type == AuthType.NONE.value or config.auth_type == "none"
        auth_header = config.auth_header
        auth_prefix = config.auth_prefix
        default_headers = config.default_headers
        health = config.health_endpoint

    cred = CredentialStore().retrieve(provider)

    if not cred and not no_auth:
        console.print(f"[red]No credential stored for '{provider}'.[/red]")
        show_auth_docs(provider, context="missing")
        raise typer.Exit(1)

    token = cred.get("access_token", "") if cred else ""

    return ResolvedConnection(
        url=url,
        auth_header=auth_header,
        auth_prefix=auth_prefix,
        token=token,
        no_auth=no_auth,
        default_headers=default_headers,
        health_endpoint=health,
    )


def show_auth_docs(provider: str, *, context: str = "auth") -> None:
    """Look up auth documentation for a provider and print it to the console.

    Called whenever authentication fails or the user is about to enter a token,
    so they know exactly where to find credentials and how to get them.

    context:
      "setup"   \u2014 printed before the token prompt (pipeline auth set)
      "missing" \u2014 printed when no credential is stored and we bail
      "healing" \u2014 printed when self-healing exhausts all auth formats
    """
    from data_pipeline.connectors.llm_discovery import discover_auth_docs
    from data_pipeline.connectors.registry import provider_registry

    preset = provider_registry.get_preset(provider.lower())
    docs_url = preset.docs_url if preset else None

    auth_info = discover_auth_docs(provider)

    if context == "setup":
        console.print(f"\n[bold]Where to find your {provider} credentials:[/bold]")
    elif context == "missing":
        console.print(f"\n[yellow]Need credentials for '{provider}'?[/yellow]")
    elif context == "healing":
        console.print("\n[yellow]Auth healing exhausted. You may need to update your credentials.[/yellow]")

    if auth_info:
        url = auth_info.get("docs_url") or docs_url
        instructions = auth_info.get("instructions", "")
        auth_type = auth_info.get("auth_type", "")

        if url:
            console.print(f"  [cyan]Docs:[/cyan]         {url}")
        if auth_type:
            console.print(f"  [cyan]Auth type:[/cyan]    {auth_type}")
        if instructions:
            console.print(f"  [cyan]How to get it:[/cyan] {instructions}")
    elif docs_url:
        console.print(f"  [cyan]Docs:[/cyan] {docs_url}")
    else:
        console.print(f"  [dim]Tip: search '{provider} API authentication' for credential docs[/dim]")

    console.print(f"\n  Then run: [bold]pipeline auth set {provider}[/bold]")


def resolve_request_params(config: APIConfig | Any, path: str) -> tuple[str, dict[str, Any] | None]:
    """Resolve HTTP method and JSON body based on API style and endpoint pattern.

    Returns: (method, json_body)
    """
    api_style = getattr(config, "api_style", "rest") if not isinstance(config, APIConfig) else "rest"

    if api_style == "graphql" or "graphql" in path.lower():
        return "POST", {"query": GRAPHQL_ISSUES_QUERY, "variables": {"first": 50}}

    if "mlflow/runs/search" in path:
        return "POST", {"experiment_ids": ["0", "1", "2", "3", "4"], "max_results": 5}

    if "/search" in path or "/filter" in path:
        return "POST", {"max_results": 100}

    return "GET", None
