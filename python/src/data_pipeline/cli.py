"""Production CLI for the Zero to Pipeline framework."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from data_pipeline.auth import AuthManager, CredentialStore
from data_pipeline.connectors import (
    APIConnector,
    APIDiscovery,
    SelfHealingConnector,
    provider_registry,
)
from data_pipeline.observability import configure_logging
from data_pipeline.schemas import (
    APIConfig,
    AuthType,
    ConnectionStatus,
    SourceConfig,
    SourceType,
)
from data_pipeline.sources import SourceStore

app = typer.Typer(
    name="pipeline",
    help=(
        "Zero to Pipeline: Self-configuring data ingestion for Data Engineering & MLOps.\n\n"
        "Connect to any API — MLflow, W&B, Airflow, Prometheus, or your internal tools:\n\n"
        "  pipeline source add mlflow\n"
        "  pipeline auth set mlflow\n"
        "  pipeline sync run mlflow"
    ),
    no_args_is_help=True,
)
console = Console()

# GraphQL default queries
DEFAULT_GRAPHQL_INTROSPECTION = '''
query { __schema { queryType { fields { name description } } } }
'''

GRAPHQL_ISSUES_QUERY = '''
query($first: Int) {
  issues(first: $first) {
    nodes { id title state createdAt updatedAt }
    pageInfo { hasNextPage endCursor }
  }
}
'''


def _resolve_request_params(config, path: str) -> tuple[str, dict[str, Any] | None]:
    """Resolve HTTP method and JSON body based on API style.

    Returns: (method, json_body)
    """
    # GraphQL APIs require POST with query body
    if config.api_style == "graphql" or "graphql" in path.lower():
        # For now, use a generic issues query for GraphQL
        # TODO: make this dynamic based on the requested resource
        return "POST", {"query": GRAPHQL_ISSUES_QUERY, "variables": {"first": 50}}

    # REST APIs use GET by default
    return "GET", None

source_app = typer.Typer(help="Manage data sources")
auth_app = typer.Typer(help="Manage authentication")
sync_app = typer.Typer(help="Run data syncs")

app.add_typer(source_app, name="source")
app.add_typer(auth_app, name="auth")
app.add_typer(sync_app, name="sync")


@app.callback()
def main_callback(
    log_level: Annotated[str, typer.Option(help="Log level")] = "WARNING",
    log_format: Annotated[str, typer.Option(help="Log format: json or console")] = "console",
):
    configure_logging(level=log_level, log_format=log_format)


# --- Source commands ---


@source_app.command("add")
def source_add(
    provider: Annotated[str, typer.Argument(help="Provider name (any API — mlflow, wandb, airflow, prometheus, or any internal API)")],
    base_url: Annotated[str | None, typer.Option("--base-url", help="Override base URL (e.g. for self-hosted MLflow)")] = None,
    auth_type: Annotated[str | None, typer.Option("--auth-type", help="Auth type override")] = None,
    force: Annotated[bool, typer.Option("--force", help="Force add even if source exists")] = False,
):
    """Add a new data source. Works with ANY API — known or unknown.

    For known providers (mlflow, wandb, airflow, prometheus, github, etc.),
    the system auto-discovers the API configuration. For unknown providers
    (your internal feature store, custom APIs), it infers defaults and uses
    LLM-driven discovery when available.

    The source is persisted to disk so `pipeline source list` and
    `pipeline sync run` can reference it later.

    Examples:
        pipeline source add mlflow                          # local MLflow
        pipeline source add mlflow --base-url http://ml-server:5000
        pipeline source add wandb
        pipeline source add my-feature-store --base-url https://features.internal.co
    """
    store = SourceStore()
    slug = provider.lower().replace(" ", "-")

    # Check for duplicates
    if not force and store.exists(slug):
        console.print(f"[yellow]Source '{provider}' already exists. Use --force to overwrite.[/yellow]")
        existing = store.get(slug)
        if existing:
            console.print(f"  Base URL: {existing.api.base_url if existing.api else 'N/A'}")
            console.print(f"  Status: {existing.connection_status.value}")
        return

    config = provider_registry.infer_config(provider)

    # LLM enrichment runs for ALL providers — preset or not.
    # For presets: LLM fills in gaps (new endpoints, rate limits, quirks).
    # For inferred: LLM discovers the full config.
    from data_pipeline.connectors.llm_discovery import discover_provider_config
    llm_config = discover_provider_config(provider)
    if llm_config:
        is_preset = config.source == "preset"
        # For presets, only fill fields that are empty/default
        if is_preset:
            if not config.default_endpoints and llm_config.get("default_endpoints"):
                config.default_endpoints = llm_config["default_endpoints"]
            if not config.docs_url and llm_config.get("docs_url"):
                config.docs_url = llm_config["docs_url"]
            if config.pagination_style == "unknown" and llm_config.get("pagination_style"):
                config.pagination_style = llm_config["pagination_style"]
            config.source = "preset+llm"
        else:
            # For inferred providers, LLM fully enriches the config
            if llm_config.get("base_url") and not base_url:
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

    effective_base_url = base_url or config.base_url
    effective_auth_type = auth_type or config.auth_type

    # Determine default endpoint from registry
    default_endpoint: str | None = None
    if config.default_endpoints:
        # Pick the first endpoint as the default
        default_endpoint = next(iter(config.default_endpoints.values()), None)

    # Build the SourceConfig for persistence
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

    source_label = "preset" if config.source == "preset" else "inferred"
    console.print(f"[green]Source '{config.name}' added[/green] (from: {source_label})")
    console.print(f"  Base URL: {effective_base_url}")
    console.print(f"  Auth: {effective_auth_type}")
    console.print(f"  Pagination: {config.pagination_style}")

    if config.docs_url:
        console.print(f"  Docs: {config.docs_url}")
    if default_endpoint:
        console.print(f"  Default endpoint: {default_endpoint}")

    console.print("\n[yellow]Next step:[/yellow] Store credentials:")
    console.print(f"  pipeline auth set {provider}")


@source_app.command("list")
def source_list():
    """List your added data sources (persisted)."""
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
        endpoint = source.default_endpoint or "—"
        last_sync = str(source.last_sync_at)[:19] if source.last_sync_at else "never"
        table.add_row(source.provider, base_url, status, endpoint, last_sync)

    console.print(table)


@source_app.command("list-providers")
def source_list_providers():
    """List known provider presets (available accelerators)."""
    table = Table(title="Available Provider Presets")
    table.add_column("Provider", style="cyan")
    table.add_column("Base URL", style="green")
    table.add_column("Auth", style="yellow")
    table.add_column("Pagination", style="blue")
    table.add_column("Endpoints", style="dim")

    for provider in provider_registry.known_providers:
        preset = provider_registry.get_preset(provider)
        if preset:
            endpoints = ", ".join(preset.default_endpoints.keys()) if preset.default_endpoints else "—"
            table.add_row(
                provider,
                preset.base_url,
                preset.auth_type.value,
                preset.pagination_style,
                endpoints,
            )

    console.print(table)
    console.print(
        "\n[dim]These are accelerators — any API name works. "
        "Unknown providers get auto-discovered.[/dim]"
    )


@source_app.command("remove")
def source_remove(
    provider: Annotated[str, typer.Argument(help="Provider to remove")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation")] = False,
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
    provider: Annotated[str, typer.Argument(help="Provider to discover")],
    base_url: Annotated[str | None, typer.Option("--base-url", help="Base URL to probe")] = None,
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
    provider: Annotated[str, typer.Argument(help="Provider to test connectivity")],
    base_url: Annotated[str | None, typer.Option("--base-url", help="Override base URL")] = None,
):
    """Test connection to a data source."""
    config = provider_registry.infer_config(provider)
    url = base_url or config.base_url
    health = config.health_endpoint

    store = CredentialStore()
    credential = store.retrieve(provider)

    if not credential:
        console.print(f"[red]No credential stored for '{provider}'.[/red]")
        console.print(f"Run: pipeline auth set {provider}")
        raise typer.Exit(1)

    token = credential.get("access_token", "")
    prefix = config.auth_prefix
    auth_value = f"{prefix} {token}".strip() if prefix else token

    connector = APIConnector(
        url,
        auth_value=auth_value,
        default_headers=config.default_headers,
    )

    with console.status(f"Testing connection to {url}..."):
        result = asyncio.run(connector.test_connection(health))

    if result:
        console.print(f"[green]Connection to {provider} successful![/green]")
        # Update source status if persisted
        source_store = SourceStore()
        source_store.update_status(provider.lower(), ConnectionStatus.CONNECTED)
    else:
        console.print(f"[red]Connection to {provider} failed.[/red]")
        source_store = SourceStore()
        source_store.update_status(provider.lower(), ConnectionStatus.FAILED)
        raise typer.Exit(1)


# --- Auth commands ---


@auth_app.command("login")
def auth_login(
    provider: Annotated[str, typer.Argument(help="Provider to authenticate via OAuth Device Flow")],
):
    """Authenticate a source using OAuth Device Flow (browser-based)."""

    preset = provider_registry.get_preset(provider)
    if not preset:
        console.print(f"[yellow]No preset for '{provider}' — OAuth config unknown.[/yellow]")
        console.print(f"Use: pipeline auth set {provider}")
        return

    console.print(
        "[dim]OAuth Device Flow requires provider-specific client_id registration.[/dim]"
    )
    console.print(f"[yellow]For {provider}, use API key auth instead:[/yellow]")
    console.print(f"  pipeline auth set {provider}")


@auth_app.command("set")
def auth_set(
    provider: Annotated[str, typer.Argument(help="Provider name")],
    token: Annotated[
        str | None, typer.Option("--token", help="API token (prompted if not provided)")
    ] = None,
):
    """Store an API key/token for a provider in the OS keychain."""
    if not token:
        import getpass
        token = getpass.getpass(f"Enter API token for {provider}: ")

    if not token or not token.strip():
        console.print("[red]Token cannot be empty[/red]")
        raise typer.Exit(1)

    store = CredentialStore()
    store.store(provider, {"access_token": token.strip(), "token_type": "bearer"})
    console.print(f"[green]Token stored securely for {provider}[/green]")
    console.print(f"[dim]Test the connection with: pipeline source test {provider}[/dim]")


@auth_app.command("status")
def auth_status():
    """Show authentication status for known providers."""
    table = Table(title="Auth Status")
    table.add_column("Provider", style="cyan")
    table.add_column("Status", style="green")

    store = CredentialStore()
    for provider in provider_registry.known_providers:
        has_cred = store.retrieve(provider) is not None
        status = "[green]authenticated[/green]" if has_cred else "[red]not authenticated[/red]"
        table.add_row(provider, status)

    console.print(table)


@auth_app.command("revoke")
def auth_revoke(
    provider: Annotated[str, typer.Argument(help="Provider to revoke credentials for")],
):
    """Remove stored credentials for a provider."""
    store = CredentialStore()
    if store.delete(provider):
        console.print(f"[green]Credentials revoked for {provider}[/green]")
    else:
        console.print(f"[yellow]No credentials found for {provider}[/yellow]")


# --- Sync commands ---


@sync_app.command("run")
def sync_run(
    provider: Annotated[str, typer.Argument(help="Provider to sync from")],
    path: Annotated[str | None, typer.Argument(help="API endpoint path (optional if source has default)")] = None,
    base_url: Annotated[str | None, typer.Option("--base-url", help="Override base URL")] = None,
    full: Annotated[bool, typer.Option("--full", help="Force full sync (ignore checkpoints)")] = False,
    heal: Annotated[bool, typer.Option("--heal", help="Enable self-healing on failures")] = True,
):
    """Run a data extraction from any API endpoint with self-healing.

    If no path is given, the source's default_endpoint is used (from the
    registry preset or the persisted source config).
    """
    from data_pipeline.orchestrator import CheckpointManager
    from data_pipeline.schemas import CheckpointState

    config = provider_registry.infer_config(provider)
    source_store = SourceStore()

    # Resolve endpoint path
    effective_path = path
    if not effective_path:
        # Try the persisted source config first
        persisted = source_store.get(provider.lower())
        if persisted and persisted.default_endpoint:
            effective_path = persisted.default_endpoint
        elif config.default_endpoints:
            # Fall back to first default endpoint from the preset
            effective_path = next(iter(config.default_endpoints.values()), None)

    if not effective_path:
        console.print(f"[red]No endpoint path specified and no default for '{provider}'.[/red]")
        console.print("Specify a path: pipeline sync run <provider> <path>")
        if config.default_endpoints:
            console.print(f"Available endpoints: {list(config.default_endpoints.keys())}")
        raise typer.Exit(1)

    url = base_url or config.base_url

    cred_store = CredentialStore()
    credential = cred_store.retrieve(provider)
    if not credential:
        console.print(f"[red]No credential for '{provider}'.[/red]")
        console.print(f"Run: pipeline auth set {provider}")
        raise typer.Exit(1)

    token = credential.get("access_token", "")
    prefix = config.auth_prefix

    # --- Checkpoint management ---
    checkpoint_mgr = CheckpointManager()
    pipeline_id = "sync"
    source_id = f"{provider}:{effective_path}"
    existing_checkpoint: CheckpointState | None = None

    if full:
        # Clear existing checkpoint for full sync
        checkpoint_mgr.clear(pipeline_id, source_id)
        console.print("[cyan]Full sync mode: checkpoint cleared[/cyan]")
    else:
        existing_checkpoint = checkpoint_mgr.load(pipeline_id, source_id)
        if existing_checkpoint and existing_checkpoint.cursor:
            console.print(
                f"[cyan]Resuming from checkpoint cursor: {existing_checkpoint.cursor}[/cyan]"
            )

    # Setup output directory for JSONL
    output_dir = Path.home() / ".zero-pipeline" / "output" / provider
    output_dir.mkdir(parents=True, exist_ok=True)
    resource_name = effective_path.strip("/").split("/")[-1].replace("/", "_")
    output_file = output_dir / f"{resource_name}.jsonl"

    # Resolve request method and body based on API style
    method, json_body = _resolve_request_params(config, effective_path)

    async def _run():
        nonlocal existing_checkpoint
        count = 0
        last_cursor: str | None = None

        # Open output file for writing
        with output_file.open("a", encoding="utf-8") as f:
            if heal:
                connector = SelfHealingConnector(
                    url,
                    token,
                    auth_prefix=prefix,
                    auth_header=config.auth_header,
                    default_headers=config.default_headers,
                )
                async for record in connector.extract_with_healing(
                    method,
                    effective_path,
                    json_body=json_body,
                    source_id=provider,
                    resource_type=resource_name,
                    checkpoint=existing_checkpoint,
                ):
                    count += 1
                    last_cursor = record.cursor

                    # Write to JSONL
                    f.write(json.dumps(record.model_dump(mode="json"), default=str) + "\n")

                    if count <= 3:
                        record_id = record.raw_data.get("id") or record.raw_data.get("title") or record.id
                        console.print(f"  [dim]{count}.[/dim] {record_id}")
                    elif count == 4:
                        console.print("  ...")

                    # Save checkpoint every 100 records
                    if count % 100 == 0 and last_cursor:
                        checkpoint_mgr.save(CheckpointState(
                            pipeline_id=pipeline_id,
                            source_id=source_id,
                            cursor=last_cursor,
                            last_record_id=record.id,
                            last_sync_at=datetime.now(),
                        ))

                if connector.healing_history:
                    console.print("\n[yellow]Self-healing actions taken:[/yellow]")
                    for action in connector.healing_history:
                        console.print(f"  - {action.description}")
            else:
                auth_value = f"{prefix} {token}".strip() if prefix else token
                connector = APIConnector(
                    url,
                    auth_value=auth_value,
                    default_headers=config.default_headers,
                )
                async with connector:
                    async for record in connector.paginate(
                        method,
                        effective_path,
                        json_body=json_body,
                        source_id=provider,
                        resource_type=resource_name,
                        checkpoint=existing_checkpoint,
                    ):
                        count += 1
                        last_cursor = record.cursor

                        # Write to JSONL
                        f.write(json.dumps(record.model_dump(mode="json"), default=str) + "\n")

                        # Save checkpoint every 100 records
                        if count % 100 == 0 and last_cursor:
                            checkpoint_mgr.save(CheckpointState(
                                pipeline_id=pipeline_id,
                                source_id=source_id,
                                cursor=last_cursor,
                                last_record_id=record.id,
                                last_sync_at=datetime.now(),
                            ))

        # Final checkpoint save
        if last_cursor:
            checkpoint_mgr.save(CheckpointState(
                pipeline_id=pipeline_id,
                source_id=source_id,
                cursor=last_cursor,
                last_sync_at=datetime.now(),
            ))

        # Update source last_sync_at
        source_store.update_status(
            provider.lower(),
            ConnectionStatus.CONNECTED,
            last_sync_at=datetime.now(),
        )

        return count

    console.print(f"[cyan]Syncing {provider}{effective_path}...[/cyan]")
    with console.status("Extracting..."):
        total = asyncio.run(_run())

    console.print(f"\n[green bold]Done: {total} records extracted[/green bold]")
    console.print(f"[dim]Output written to: {output_file}[/dim]")


@sync_app.command("status")
def sync_status():
    """Show sync status and checkpoint info."""
    from data_pipeline.orchestrator import CheckpointManager

    mgr = CheckpointManager()
    console.print("[dim]Checkpoint directory:[/dim]", str(mgr._dir))

    # Show existing checkpoints
    if mgr._dir.exists():
        checkpoints = list(mgr._dir.glob("*.json"))
        if checkpoints:
            table = Table(title="Active Checkpoints")
            table.add_column("Source", style="cyan")
            table.add_column("Cursor", style="green")
            table.add_column("Last Sync", style="dim")

            for cp_file in checkpoints:
                import json
                try:
                    data = json.loads(cp_file.read_text())
                    source_id = data.get("source_id", cp_file.stem)
                    cursor = data.get("cursor", "—")
                    last_sync = str(data.get("last_sync_at", "—"))[:19]
                    table.add_row(source_id, str(cursor)[:40], last_sync)
                except Exception:
                    pass

            console.print(table)
        else:
            console.print("[dim]No active checkpoints.[/dim]")


# --- Top-level commands ---


@app.command("chat")
def chat_command(
    request: Annotated[str, typer.Argument(help="Natural language request")],
):
    """Execute pipeline commands using natural language.

    Examples:
      pipeline chat "add linear as a source"
      pipeline chat "sync issues from linear"
      pipeline chat "list all my sources"
    """
    from data_pipeline.connectors.llm_discovery import plan_action

    request_lower = request.lower()

    # Pattern matching for common intents
    if any(word in request_lower for word in ["add", "connect", "configure"]):
        # Extract provider name (simple heuristic)
        words = request.split()
        for i, word in enumerate(words):
            if word.lower() in ["add", "connect", "configure"]:
                if i + 1 < len(words):
                    provider = words[i + 1]
                    console.print(f"[cyan]Adding source: {provider}[/cyan]")
                    # Call source_add logic
                    from typer.testing import CliRunner
                    runner = CliRunner()
                    result = runner.invoke(app, ["source", "add", provider])
                    return

    elif any(word in request_lower for word in ["sync", "fetch", "get", "extract"]):
        # Try to extract provider from request
        store = SourceStore()
        sources = store.list()

        if not sources:
            console.print("[yellow]No sources configured. Add a source first:[/yellow]")
            console.print("  pipeline source add <provider>")
            return

        # Look for source names in the request
        matched_source = None
        for source in sources:
            if source.provider in request_lower or source.name.lower() in request_lower:
                matched_source = source
                break

        if matched_source:
            console.print(f"[cyan]Syncing from {matched_source.provider}...[/cyan]")
            from typer.testing import CliRunner
            runner = CliRunner()
            result = runner.invoke(app, ["sync", "run", matched_source.provider])
            return
        else:
            console.print("[yellow]Could not identify source from request.[/yellow]")
            console.print("[yellow]Available sources:[/yellow]")
            for source in sources:
                console.print(f"  - {source.provider}")
            return

    elif any(word in request_lower for word in ["list", "show", "display"]):
        console.print("[cyan]Listing sources...[/cyan]")
        from typer.testing import CliRunner
        runner = CliRunner()
        result = runner.invoke(app, ["source", "list"])
        return

    # Fallback: try LLM-based planning if available
    plan = plan_action(request, "pipeline", {
        "commands": ["source add", "source list", "sync run"],
    })

    if plan and plan.get("description"):
        console.print(f"[yellow]Suggestion:[/yellow] {plan['description']}")
        if plan.get("path"):
            console.print(f"[dim]Try: pipeline {plan['path']}[/dim]")
    else:
        console.print("[yellow]Could not understand request.[/yellow]")
        console.print("[dim]Try commands like:[/dim]")
        console.print("  pipeline source add <provider>")
        console.print("  pipeline sync run <provider>")
        console.print("  pipeline source list")


@app.command("doctor")
def doctor():
    """Run health checks on the pipeline system."""
    checks = [
        ("Keyring accessible", _check_keyring),
        ("Provider registry loaded", _check_registry),
        ("Checkpoint dir writable", _check_checkpoints),
        ("Source store accessible", _check_source_store),
    ]

    all_ok = True
    for name, check_fn in checks:
        try:
            check_fn()
            console.print(f"  [green]OK[/green]  {name}")
        except Exception as e:
            console.print(f"  [red]FAIL[/red] {name}: {e}")
            all_ok = False

    if all_ok:
        console.print("\n[green bold]All checks passed![/green bold]")
    else:
        console.print("\n[red bold]Some checks failed.[/red bold]")
        raise typer.Exit(1)


@app.command("mcp-server")
def mcp_server():
    """Start the MCP server for AI assistant integration."""
    from data_pipeline.mcp import PipelineMCPServer
    server = PipelineMCPServer()
    server.run()


def _check_keyring():
    import keyring
    keyring.get_password("zero-pipeline-check", "test")


def _check_registry():
    providers = provider_registry.known_providers
    if not providers:
        raise RuntimeError("No providers in registry")


def _check_checkpoints():
    from data_pipeline.config import settings
    settings.checkpoint_dir.mkdir(parents=True, exist_ok=True)
    test_file = settings.checkpoint_dir / ".write_test"
    test_file.write_text("ok")
    test_file.unlink()


def _check_source_store():
    store = SourceStore()
    # Verify the store directory is writable
    store._base_dir.mkdir(parents=True, exist_ok=True)
    test_file = store._base_dir / ".write_test"
    test_file.write_text("ok")
    test_file.unlink()


def main():
    app()


if __name__ == "__main__":
    main()
