"""Source management CLI commands."""

from __future__ import annotations

import asyncio
import uuid

import typer

from data_pipeline.cli.docker import LOCAL_CONFIGS, ensure_local
from data_pipeline.cli.helpers import console, resolve_for_provider, show_auth_docs
from data_pipeline.connectors import (
    APIDiscovery,
    provider_registry,
)
from data_pipeline.schemas import (
    APIConfig,
    AuthType,
    ConnectionStatus,
    SourceConfig,
    SourceType,
)
from data_pipeline.sources import SourceStore

source_app = typer.Typer(help="Manage data sources")


@source_app.command("add")
def source_add(
    provider: str = typer.Argument(help="Provider name (any API \u2014 mlflow, wandb, airflow, prometheus, or any internal API)"),
    base_url: str | None = typer.Option(None, "--base-url", help="Override base URL (e.g. for self-hosted MLflow)"),
    auth_type: str | None = typer.Option(None, "--auth-type", help="Auth type override"),
    local: bool = typer.Option(False, "--local", help="Start a local Docker instance of this provider automatically"),
    force: bool = typer.Option(False, "--force", help="Force add even if source exists"),
):
    """Add a new data source. Works with ANY API \u2014 known or unknown."""
    store = SourceStore()
    slug = provider.lower().replace(" ", "-")

    if not force and store.exists(slug):
        console.print(f"[yellow]Source '{provider}' already exists. Use --force to overwrite.[/yellow]")
        existing = store.get(slug)
        if existing:
            console.print(f"  Base URL: {existing.api.base_url if existing.api else 'N/A'}")
            console.print(f"  Status: {existing.connection_status.value}")
        return

    if local:
        resolved = _resolve_local_config(provider)
        if resolved is None:
            raise typer.Exit(1)
        base_url, effective_auth_type, _ = resolved
        if auth_type is None:
            auth_type = effective_auth_type

    config = provider_registry.infer_config(provider)
    _llm_enrich_config(provider, config)

    effective_base_url = base_url or config.base_url
    effective_auth_type = auth_type or config.auth_type

    default_endpoint: str | None = None
    if config.default_endpoints:
        default_endpoint = next(iter(config.default_endpoints.values()), None)

    source = SourceConfig(
        id=str(uuid.uuid4()),
        name=config.name,
        slug=slug,
        source_type=SourceType.API,
        provider=provider.lower(),
        enabled=True,
        connection_status=ConnectionStatus.UNTESTED,
        default_endpoint=default_endpoint,
        api=APIConfig(
            base_url=effective_base_url,
            auth_type=AuthType(effective_auth_type) if effective_auth_type in AuthType.__members__.values() else AuthType.API_KEY,
            auth_header=config.auth_header,
            auth_prefix=config.auth_prefix,
            rate_limit_rpm=config.rate_limit_rpm,
            pagination_style=config.pagination_style,
            default_headers=config.default_headers,
        ),
        tagline=config.docs_url,
    )

    store.save(source)
    console.print(f"[dim]  \u2713 Saved to: ~/.zero-pipeline/sources/{slug}/config.json[/dim]")

    source_label = "preset+llm" if config.source == "preset+llm" else ("llm_discovered" if config.source == "llm_discovered" else "inferred")
    console.print(f"\n[green bold]Source '{config.name}' added[/green bold] [dim](from: {source_label})[/dim]")
    console.print(f"  Base URL: {effective_base_url}")
    console.print(f"  Auth: {effective_auth_type}")
    console.print(f"  Pagination: {config.pagination_style}")

    if config.docs_url:
        console.print(f"  Docs: {config.docs_url}")
    if default_endpoint:
        console.print(f"  Default endpoint: {default_endpoint}")

    if effective_auth_type == AuthType.NONE:
        console.print("\n[green]No credentials needed[/green] \u2014 this source runs without authentication.")
    else:
        console.print("\n[yellow]Next step:[/yellow] Store credentials:")
        show_auth_docs(provider, context="setup")


def _resolve_local_config(provider: str) -> tuple[str, str, dict] | None:
    """Resolve Docker-based local config for a provider, or ask LLM."""
    slug_lower = provider.lower()
    cfg = LOCAL_CONFIGS.get(slug_lower)

    if cfg is None:
        from data_pipeline.connectors.llm_discovery import get_llm_provider
        llm = get_llm_provider()
        if llm:
            console.print(f"[dim]  \u2192 LLM: looking up Docker image for '{provider}'...[/dim]")
            result = llm.complete(
                f'What is the official Docker image and default port for "{provider}"? '
                "Respond with ONLY JSON: "
                '{"image": "org/image:tag", "port": 8080, "auth_type": "none|api_key|basic", '
                '"run_cmd": [], "health": "/health"}'
            )
            if result.success and result.parsed:
                cfg_data = result.parsed
                console.print(f"[dim]  \u2713 LLM: found image={cfg_data.get('image')}, port={cfg_data.get('port')}[/dim]")
            else:
                console.print(f"[yellow]  \u26a0 No local Docker config found for '{provider}'.[/yellow]")
                console.print(f"  [dim]Try: pipeline source add {provider} --base-url http://127.0.0.1:<port>[/dim]")
                return None
        else:
            console.print(f"[yellow]  \u26a0 '{provider}' has no local config and LLM is unavailable.[/yellow]")
            return None

        port = int(cfg_data["port"])
        image = cfg_data["image"]
        run_cmd = tuple(cfg_data.get("run_cmd", []))
        inferred_auth = cfg_data.get("auth_type", "none")
    else:
        port = cfg.port
        image = cfg.image
        run_cmd = cfg.run_cmd
        inferred_auth = cfg.auth_type

    console.print(f"\n[bold]Setting up {provider} locally[/bold]")
    console.print(f"[dim]  image: {image}[/dim]")
    console.print(f"[dim]  port:  {port}[/dim]")
    note = cfg.note if cfg and cfg.note else (cfg_data.get("note") if not cfg and cfg_data else None)
    if note:
        console.print(f"[dim]  note:  {note}[/dim]")

    resolved_url = ensure_local(provider, port, image, run_cmd)
    if resolved_url is None:
        return None

    return resolved_url, inferred_auth, {}


def _llm_enrich_config(provider: str, config) -> None:
    """Enrich inferred config with LLM-discovered details."""
    from data_pipeline.connectors.llm_discovery import discover_provider_config

    if config.source == "preset":
        console.print(f"[dim]  \u2713 Registry: preset found for '{provider}'[/dim]")
        console.print("[dim]  \u2192 LLM: enriching with deeper knowledge...[/dim]")
    else:
        console.print(f"[dim]  \u2192 LLM: '{provider}' not in registry \u2014 discovering API config...[/dim]")

    llm_config = discover_provider_config(provider)
    if not llm_config:
        return

    is_preset = config.source == "preset"
    if is_preset:
        if not config.default_endpoints and llm_config.get("default_endpoints"):
            config.default_endpoints = llm_config["default_endpoints"]
        if not config.docs_url and llm_config.get("docs_url"):
            config.docs_url = llm_config["docs_url"]
        if config.pagination_style == "unknown" and llm_config.get("pagination_style"):
            config.pagination_style = llm_config["pagination_style"]
        config.source = "preset+llm"
    else:
        if llm_config.get("base_url"):
            config.base_url = llm_config["base_url"]
        if llm_config.get("auth_type"):
            config.auth_type = llm_config["auth_type"]
        if llm_config.get("pagination_style"):
            config.pagination_style = llm_config["pagination_style"]
        if llm_config.get("api_style"):
            config.api_style = llm_config["api_style"]
        if llm_config.get("default_endpoints"):
            config.default_endpoints = llm_config["default_endpoints"]
        if llm_config.get("docs_url"):
            config.docs_url = llm_config["docs_url"]
        config.source = "llm_discovered"
        console.print(f"[dim]  \u2713 LLM: discovered auth={config.auth_type}, pagination={config.pagination_style}[/dim]")


@source_app.command("list")
def source_list():
    """List your added data sources (persisted)."""
    from rich.table import Table

    store = SourceStore()
    sources = store.list()

    if not sources:
        console.print("[dim]No sources added yet. Run: pipeline source add <provider>[/dim]")
        console.print("[dim]To see available presets: pipeline source list-providers[/dim]")
        return

    table = Table(title="Added Sources")
    table.add_column("Provider", style="cyan")
    table.add_column("Base URL", style="green")
    table.add_column("Status", style="yellow")
    table.add_column("Default Endpoint", style="blue")
    table.add_column("Last Sync", style="dim")

    for source in sources:
        base_url = source.api.base_url if source.api else "N/A"
        status = source.connection_status.value
        endpoint = source.default_endpoint or "\u2014"
        last_sync = str(source.last_sync_at)[:19] if source.last_sync_at else "never"
        table.add_row(source.provider, base_url, status, endpoint, last_sync)

    console.print(table)


@source_app.command("list-providers")
def source_list_providers():
    """List known provider presets (available accelerators)."""
    from rich.table import Table

    table = Table(title="Available Provider Presets")
    table.add_column("Provider", style="cyan")
    table.add_column("Base URL", style="green")
    table.add_column("Auth", style="yellow")
    table.add_column("Pagination", style="blue")
    table.add_column("Endpoints", style="dim")

    for provider in provider_registry.known_providers:
        preset = provider_registry.get_preset(provider)
        if preset:
            endpoints = ", ".join(preset.default_endpoints.keys()) if preset.default_endpoints else "\u2014"
            table.add_row(
                provider,
                preset.base_url,
                preset.auth_type.value,
                preset.pagination_style,
                endpoints,
            )

    console.print(table)
    console.print(
        "\n[dim]These are accelerators \u2014 any API name works. "
        "Unknown providers get auto-discovered.[/dim]"
    )


@source_app.command("remove")
def source_remove(
    provider: str = typer.Argument(help="Provider to remove"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Remove a data source from the pipeline."""
    store = SourceStore()
    slug = provider.lower().replace(" ", "-")

    if not store.exists(slug):
        console.print(f"[red]Source '{provider}' not found.[/red]")
        raise typer.Exit(1)

    if not force:
        confirm = typer.confirm(f"Remove source '{provider}'?")
        if not confirm:
            console.print("[dim]Cancelled.[/dim]")
            return

    if store.delete(slug):
        console.print(f"[green]Source '{provider}' removed successfully.[/green]")
    else:
        console.print(f"[red]Failed to remove source '{provider}'.[/red]")
        raise typer.Exit(1)


@source_app.command("discover")
def source_discover(
    provider: str = typer.Argument(help="Provider to discover"),
    base_url: str | None = typer.Option(None, "--base-url", help="Base URL to probe"),
):
    """Probe an API to discover its endpoints and capabilities."""
    discovery = APIDiscovery(provider, base_url=base_url)

    async def _discover():
        return await discovery.discover()

    with console.status(f"Discovering {provider} API..."):
        config = asyncio.run(_discover())

    console.print(f"[bold]Discovery results for {provider}:[/bold]")
    console.print(f"  Source: {config.get('source', 'unknown')}")
    console.print(f"  Base URL: {config['base_url']}")
    console.print(f"  Reachable: {config.get('reachable', 'untested')}")
    console.print(f"  Pagination: {config.get('pagination_style', 'unknown')}")
    if config.get("rate_limit_rpm"):
        console.print(f"  Rate limit: {config['rate_limit_rpm']} req/min")
    if config.get("default_endpoints"):
        console.print(f"  Endpoints: {config['default_endpoints']}")
    if config.get("error"):
        console.print(f"  [red]Error: {config['error']}[/red]")


@source_app.command("test")
def source_test(
    provider: str = typer.Argument(help="Provider to test connectivity"),
    base_url: str | None = typer.Option(None, "--base-url", help="Override base URL"),
):
    """Test connection to a data source."""
    import httpx as _httpx

    rc = resolve_for_provider(provider, base_url=base_url)

    async def _test():
        if rc.token and rc.auth_prefix:
            auth_value = f"{rc.auth_prefix} {rc.token}"
        elif rc.token:
            auth_value = rc.token
        else:
            auth_value = ""

        headers = {**rc.default_headers}
        if auth_value:
            headers[rc.auth_header] = auth_value

        async with _httpx.AsyncClient(base_url=rc.url, headers=headers, timeout=10) as client:
            paths = [rc.health_endpoint] if rc.health_endpoint != "/" else ["/"]
            if "/" not in paths:
                paths.append("/")

            for path in paths:
                try:
                    resp = await client.get(path)
                    if resp.status_code < 500:
                        return True
                except _httpx.HTTPStatusError as e:
                    if e.response.status_code < 500:
                        return True
                except (_httpx.ConnectError, _httpx.TimeoutException):
                    return False
                except Exception:
                    pass

            return False

    with console.status(f"Testing connection to {rc.url}..."):
        result = asyncio.run(_test())

    if result:
        console.print(f"[green]Connection to {provider} successful![/green]")
        SourceStore().update_status(provider.lower(), ConnectionStatus.CONNECTED)
    else:
        console.print(f"[red]Connection to {provider} failed.[/red]")
        console.print(f"[dim]Check that the server is reachable at {rc.url}[/dim]")
        SourceStore().update_status(provider.lower(), ConnectionStatus.FAILED)
        raise typer.Exit(1)
