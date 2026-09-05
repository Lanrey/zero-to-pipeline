"""Production CLI for the Zero to Pipeline framework."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from typer._click.exceptions import UsageError as ClickUsageError
from rich.console import Console
from rich.table import Table

from data_pipeline.auth import CredentialStore
from data_pipeline.connectors import (
    APIDiscovery,
    ConnectorError,
    MlflowRunsPagination,
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
    """Resolve HTTP method and JSON body based on API style and endpoint pattern.

    Returns: (method, json_body)
    """
    # GraphQL APIs require POST with query body
    if config.api_style == "graphql" or "graphql" in path.lower():
        return "POST", {"query": GRAPHQL_ISSUES_QUERY, "variables": {"first": 50}}

    # MLflow runs/search requires POST with experiment_ids to return results
    if "mlflow/runs/search" in path:
        return "POST", {"experiment_ids": ["0", "1", "2", "3", "4"], "max_results": 5}

    # MLflow other search/filter endpoints
    if "/search" in path or "/filter" in path:
        return "POST", {"max_results": 100}

    # REST APIs use GET by default
    return "GET", None

source_app = typer.Typer(help="Manage data sources")
auth_app = typer.Typer(help="Manage authentication")
sync_app = typer.Typer(help="Run data syncs")


def _show_auth_docs(provider: str, *, context: str = "auth") -> None:
    """Look up auth documentation for a provider and print it to the console.

    Called whenever authentication fails or the user is about to enter a token,
    so they know exactly where to find credentials and how to get them.

    context:
      "setup"   — printed before the token prompt (pipeline auth set)
      "missing" — printed when no credential is stored and we bail
      "healing" — printed when self-healing exhausts all auth formats
    """
    from data_pipeline.connectors.llm_discovery import discover_auth_docs
    from data_pipeline.connectors.registry import provider_registry

    # Registry preset may already have a docs URL — use it as fast-path
    preset = provider_registry.get_preset(provider.lower())
    docs_url = preset.docs_url if preset else None

    # Always call the LLM for instructions — registry only has the URL
    auth_info = discover_auth_docs(provider)

    if context == "setup":
        console.print(f"\n[bold]Where to find your {provider} credentials:[/bold]")
    elif context == "missing":
        console.print(f"\n[yellow]Need credentials for '{provider}'?[/yellow]")
    elif context == "healing":
        console.print(f"\n[yellow]Auth healing exhausted. You may need to update your credentials.[/yellow]")

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


# Known local deployment configs: Docker image, port, run args, auth mode
# Used by --local flag to start the container automatically
_LOCAL_CONFIGS: dict[str, dict] = {
    "mlflow": {
        "image": "ghcr.io/mlflow/mlflow",
        "port": 5000,
        "run_cmd": ["mlflow", "server", "--host", "0.0.0.0"],
        "auth_type": "none",
        "health": "/",
    },
    "prometheus": {
        "image": "prom/prometheus",
        "port": 9090,
        "run_cmd": [],
        "auth_type": "none",
        "health": "/-/healthy",
    },
    "grafana": {
        "image": "grafana/grafana",
        "port": 3000,
        "run_cmd": [],
        "auth_type": "none",
        "health": "/api/health",
    },
    "airflow": {
        "image": "apache/airflow",
        "port": 8080,
        "run_cmd": ["standalone"],
        "auth_type": "basic",
        "health": "/health",
        "note": "Default credentials: airflow / airflow",
    },
    "prefect": {
        "image": "prefecthq/prefect",
        "port": 4200,
        "run_cmd": ["prefect", "server", "start", "--host", "0.0.0.0"],
        "auth_type": "none",
        "health": "/api/health",
    },
    "wandb": {
        "image": "wandb/local",
        "port": 8080,
        "run_cmd": [],
        "auth_type": "api_key",
        "health": "/healthz",
        "note": "W&B local server — requires a license for production use",
    },
}


def _ensure_local(provider: str, port: int, image: str, run_cmd: list[str]) -> str | None:
    """Ensure the provider's Docker container is running.

    Returns the base URL if successful, None if setup failed.
    Self-heals by:
    - Pulling the image if not present
    - Starting the container if not running
    - Waiting up to 30s for the health endpoint to respond
    """
    import subprocess
    import time
    import httpx

    slug = provider.lower()
    container_name = f"zero-pipeline-{slug}"
    base_url = f"http://127.0.0.1:{port}"

    # ── Check Docker is available ────────────────────────────────────────────
    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True, timeout=10)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        console.print("[red]  ✗ Docker not found.[/red] Install Docker Desktop and try again.")
        console.print("  [dim]https://docs.docker.com/get-docker/[/dim]")
        return None

    # ── Check if container is already running ────────────────────────────────
    result = subprocess.run(
        ["docker", "ps", "--filter", f"name={container_name}", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=10,
    )
    already_running = container_name in result.stdout

    if already_running:
        console.print(f"[dim]  ✓ Container '{container_name}' already running[/dim]")
    else:
        # ── Check if port is already in use ──────────────────────────────────
        port_check = subprocess.run(
            ["docker", "ps", "--filter", f"publish={port}", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10,
        )
        if port_check.stdout.strip():
            other = port_check.stdout.strip()
            console.print(f"[yellow]  ⚠ Port {port} is in use by container '{other}'.[/yellow]")
            console.print(f"[dim]  Self-healing: using that container as the source[/dim]")
        else:
            # ── Pull image ────────────────────────────────────────────────────
            console.print(f"[dim]  → Pulling {image}...[/dim]")
            pull = subprocess.run(
                ["docker", "pull", image],
                capture_output=True, text=True, timeout=300,
            )
            if pull.returncode != 0:
                console.print(f"[red]  ✗ Failed to pull {image}[/red]")
                console.print(f"  [dim]{pull.stderr.strip()[:200]}[/dim]")
                return None

            # ── Start container ───────────────────────────────────────────────
            console.print(f"[dim]  → Starting {container_name} on port {port}...[/dim]")
            docker_run = ["docker", "run", "-d", "--name", container_name,
                          "-p", f"127.0.0.1:{port}:{port}"]
            if run_cmd:
                docker_run += [image] + run_cmd
            else:
                docker_run += [image]

            start = subprocess.run(docker_run, capture_output=True, text=True, timeout=60)
            if start.returncode != 0:
                err = start.stderr.strip()
                # Self-heal: container exists but stopped — restart it
                if "already in use" in err or "Conflict" in err:
                    console.print(f"[dim]  Self-healing: container exists but stopped — restarting...[/dim]")
                    subprocess.run(["docker", "start", container_name],
                                   capture_output=True, timeout=30)
                else:
                    console.print(f"[red]  ✗ Failed to start container[/red]")
                    console.print(f"  [dim]{err[:200]}[/dim]")
                    return None

    # ── Wait for health endpoint ──────────────────────────────────────────────
    cfg = _LOCAL_CONFIGS.get(provider, {})
    health_path = cfg.get("health", "/")
    console.print(f"[dim]  → Waiting for {base_url} to become ready...[/dim]")

    for attempt in range(15):
        try:
            r = httpx.get(f"{base_url}{health_path}", timeout=2)
            if r.status_code < 500:
                console.print(f"[green]  ✓ {provider} is ready at {base_url}[/green]")
                return base_url
        except Exception:
            pass
        time.sleep(2)

    console.print(f"[yellow]  ⚠ {provider} did not become ready within 30s.[/yellow]")
    console.print(f"  [dim]The source has been added — retry: pipeline source test {provider}[/dim]")
    return base_url  # Still return — let the user test manually


@source_app.command("add")
def source_add(
    provider: Annotated[str, typer.Argument(help="Provider name (any API — mlflow, wandb, airflow, prometheus, or any internal API)")],
    base_url: Annotated[str | None, typer.Option("--base-url", help="Override base URL (e.g. for self-hosted MLflow)")] = None,
    auth_type: Annotated[str | None, typer.Option("--auth-type", help="Auth type override")] = None,
    local: Annotated[bool, typer.Option("--local", help="Start a local Docker instance of this provider automatically")] = False,
    force: Annotated[bool, typer.Option("--force", help="Force add even if source exists")] = False,
):
    """Add a new data source. Works with ANY API — known or unknown.

    For known providers (mlflow, wandb, airflow, prometheus, github, etc.),
    the system auto-discovers the API configuration. For unknown providers
    (your internal feature store, custom APIs), it infers defaults and uses
    LLM-driven discovery when available.

    Use --local to automatically pull and start a Docker container for
    providers that support local deployment (mlflow, prometheus, grafana,
    airflow, prefect, wandb). Docker must be installed.

    Examples:
        pipeline source add mlflow --local                  # start Docker, add, done
        pipeline source add prometheus --local              # no auth needed
        pipeline source add mlflow --base-url http://ml-server:5000
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

    # ── --local: pull Docker image, start container, resolve URL + auth ──────
    if local:
        slug_lower = provider.lower()
        local_cfg = _LOCAL_CONFIGS.get(slug_lower)

        if local_cfg is None:
            # Unknown provider — ask LLM for Docker details
            from data_pipeline.connectors.llm_discovery import get_llm_provider
            llm = get_llm_provider()
            if llm:
                console.print(f"[dim]  → LLM: looking up Docker image for '{provider}'...[/dim]")
                result = llm.complete(
                    f'What is the official Docker image and default port for "{provider}"? '
                    'Respond with ONLY JSON: '
                    '{"image": "org/image:tag", "port": 8080, "auth_type": "none|api_key|basic", '
                    '"run_cmd": [], "health": "/health"}'
                )
                if result.success and result.parsed:
                    local_cfg = result.parsed
                    console.print(f"[dim]  ✓ LLM: found image={local_cfg.get('image')}, port={local_cfg.get('port')}[/dim]")
                else:
                    console.print(f"[yellow]  ⚠ No local Docker config found for '{provider}'.[/yellow]")
                    console.print(f"  [dim]Try: pipeline source add {provider} --base-url http://127.0.0.1:<port>[/dim]")
                    return
            else:
                console.print(f"[yellow]  ⚠ '{provider}' has no local config and LLM is unavailable.[/yellow]")
                return

        port = int(local_cfg["port"])
        image = local_cfg["image"]
        run_cmd = local_cfg.get("run_cmd", [])

        console.print(f"\n[bold]Setting up {provider} locally[/bold]")
        console.print(f"[dim]  image: {image}[/dim]")
        console.print(f"[dim]  port:  {port}[/dim]")
        if local_cfg.get("note"):
            console.print(f"[dim]  note:  {local_cfg['note']}[/dim]")

        resolved_url = _ensure_local(provider, port, image, run_cmd)
        if resolved_url is None:
            raise typer.Exit(1)

        # Override flags from local config
        base_url = resolved_url
        if auth_type is None:
            auth_type = local_cfg.get("auth_type", "none")

    config = provider_registry.infer_config(provider)

    # LLM enrichment runs for ALL providers — preset or not.
    # For presets: LLM fills in gaps (new endpoints, rate limits, quirks).
    # For inferred: LLM discovers the full config.
    from data_pipeline.connectors.llm_discovery import discover_provider_config
    if config.source == "preset":
        console.print(f"[dim]  ✓ Registry: preset found for '{provider}'[/dim]")
        console.print(f"[dim]  → LLM: enriching with deeper knowledge...[/dim]")
    else:
        console.print(f"[dim]  → LLM: '{provider}' not in registry — discovering API config...[/dim]")
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
            console.print(f"[dim]  ✓ LLM: discovered auth={config.auth_type}, pagination={config.pagination_style}[/dim]")

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
    console.print(f"[dim]  ✓ Saved to: ~/.zero-pipeline/sources/{slug}/config.json[/dim]")

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
        console.print("\n[green]No credentials needed[/green] — this source runs without authentication.")
    else:
        console.print("\n[yellow]Next step:[/yellow] Store credentials:")
        _show_auth_docs(provider, context="setup")


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

    # Prefer persisted source config over registry defaults — the user may have
    # overridden base-url, auth-type, or other fields when they ran source add.
    source_store = SourceStore()
    persisted = source_store.get(provider.lower())

    if persisted and persisted.api:
        url = base_url or persisted.api.base_url
        health = config.health_endpoint
        no_auth = persisted.api.auth_type == AuthType.NONE
        auth_header = persisted.api.auth_header
        auth_prefix = persisted.api.auth_prefix
        default_headers = persisted.api.default_headers
    else:
        url = base_url or config.base_url
        health = config.health_endpoint
        no_auth = config.auth_type == AuthType.NONE.value or config.auth_type == "none"
        auth_header = config.auth_header
        auth_prefix = config.auth_prefix
        default_headers = config.default_headers

    cred_store = CredentialStore()
    credential = cred_store.retrieve(provider)

    if not credential and not no_auth:
        console.print(f"[red]No credential stored for '{provider}'.[/red]")
        _show_auth_docs(provider, context="missing")
        raise typer.Exit(1)

    token = credential.get("access_token", "") if credential else ""

    async def _test():
        import httpx as _httpx

        # Build auth header value — empty credential means no auth header sent
        if token and auth_prefix:
            auth_value = f"{auth_prefix} {token}"
        elif token:
            auth_value = token
        else:
            auth_value = ""

        headers = {**default_headers}
        if auth_value:
            headers[auth_header] = auth_value

        # Use a plain httpx client — no tenacity retries, no raise_for_status.
        # We just want to know if the server responds at all.
        async with _httpx.AsyncClient(base_url=url, headers=headers, timeout=10) as client:
            paths = [health] if health != "/" else ["/"]
            if "/" not in paths:
                paths.append("/")

            for path in paths:
                try:
                    resp = await client.get(path)
                    # Any response below 500 means the server is up
                    if resp.status_code < 500:
                        return True
                    # 5xx → server error, try next path
                except _httpx.HTTPStatusError as e:
                    if e.response.status_code < 500:
                        return True
                except (_httpx.ConnectError, _httpx.TimeoutException):
                    return False  # network unreachable — no point trying other paths
                except Exception:
                    pass  # try next path

        # If we had a stored credential and got this far, try with self-healing
        # to surface proper auth format info
        if token:
            connector = SelfHealingConnector(
                url, credential=token,
                auth_header=auth_header, auth_prefix=auth_prefix,
                default_headers=default_headers,
            )
            try:
                async with _httpx.AsyncClient(base_url=url, headers=headers, timeout=10) as client:
                    resp = await client.get("/")
                    return resp.status_code < 500
            except Exception:
                pass

        return False

    with console.status(f"Testing connection to {url}..."):
        result = asyncio.run(_test())

    if result:
        console.print(f"[green]Connection to {provider} successful![/green]")
        source_store.update_status(provider.lower(), ConnectionStatus.CONNECTED)
    else:
        console.print(f"[red]Connection to {provider} failed.[/red]")
        console.print(f"[dim]Check that the server is reachable at {url}[/dim]")
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
        # Show auth docs before prompting — so the user knows where to find their token
        _show_auth_docs(provider, context="setup")
        console.print()
        token = getpass.getpass(f"Enter API token for {provider}: ")

    if not token or not token.strip():
        console.print("[red]Token cannot be empty[/red]")
        raise typer.Exit(1)

    store = CredentialStore()
    console.print(f"[dim]  → Storing token in OS keychain (never written to disk)...[/dim]")
    store.store(provider, {"access_token": token.strip(), "token_type": "bearer"})
    console.print(f"[green]✓ Token stored securely for {provider}[/green]")
    console.print(f"[dim]  Location: macOS Keychain / Linux Secret Service[/dim]")
    console.print(f"[dim]  Test: pipeline source test {provider}[/dim]")


@auth_app.command("status")
def auth_status():
    """Show authentication status for all sources — preset and user-added."""
    table = Table(title="Auth Status")
    table.add_column("Provider", style="cyan")
    table.add_column("Auth type", style="dim")
    table.add_column("Status")

    cred_store = CredentialStore()
    source_store = SourceStore()

    # Build a unified list: registry presets + any user-added sources not in registry
    seen: set[str] = set()
    rows: list[tuple[str, str, str]] = []

    # Registry presets first
    for provider in provider_registry.known_providers:
        seen.add(provider)
        preset = provider_registry.get_preset(provider)
        auth_type = preset.auth_type.value if preset else "api_key"
        if preset and preset.auth_type == AuthType.NONE:
            status = "[dim]no auth required[/dim]"
        else:
            has_cred = cred_store.retrieve(provider) is not None
            status = "[green]authenticated[/green]" if has_cred else "[red]not authenticated[/red]"
        rows.append((provider, auth_type, status))

    # User-added sources not in registry
    for source in source_store.list():
        if source.provider not in seen:
            seen.add(source.provider)
            auth_type = source.api.auth_type.value if source.api else "api_key"
            if source.api and source.api.auth_type == AuthType.NONE:
                status = "[dim]no auth required[/dim]"
            else:
                has_cred = cred_store.retrieve(source.provider) is not None
                status = "[green]authenticated[/green]" if has_cred else "[red]not authenticated[/red]"
            rows.append((source.provider, auth_type, status))

    for provider, auth_type, status in sorted(rows):
        table.add_row(provider, auth_type, status)

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

    # Prefer persisted source config over registry defaults for all connection params
    persisted_for_auth = source_store.get(provider.lower())
    if persisted_for_auth and persisted_for_auth.api:
        p = persisted_for_auth.api
        url = base_url or p.base_url
        no_auth = p.auth_type == AuthType.NONE
        auth_header = p.auth_header
        auth_prefix = p.auth_prefix
        default_headers = p.default_headers
    else:
        url = base_url or config.base_url
        no_auth = config.auth_type == AuthType.NONE.value or config.auth_type == "none"
        auth_header = config.auth_header
        auth_prefix = config.auth_prefix
        default_headers = config.default_headers

    cred_store = CredentialStore()
    credential = cred_store.retrieve(provider)

    if not credential and not no_auth:
        console.print(f"[red]No credential for '{provider}'.[/red]")
        _show_auth_docs(provider, context="missing")
        raise typer.Exit(1)

    token = credential.get("access_token", "") if credential else ""
    prefix = "" if no_auth else auth_prefix

    # --- Checkpoint management ---
    checkpoint_mgr = CheckpointManager()
    pipeline_id = "sync"
    # Sanitize source_id — colons and slashes are invalid in filenames on macOS
    source_id = f"{provider}_{effective_path}".replace("/", "_").replace(":", "_")
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

    # MLflow runs/search uses a special cursor-in-body pagination
    pagination_override = None
    if "mlflow/runs/search" in effective_path:
        pagination_override = MlflowRunsPagination(page_size=5)

    async def _run():
        nonlocal existing_checkpoint
        count = 0
        last_cursor: str | None = None

        # Always use SelfHealingConnector — it adapts auth format, infers pagination,
        # and resumes from checkpoints automatically regardless of the source.
        with output_file.open("a", encoding="utf-8") as f:
            connector = SelfHealingConnector(
                url,
                token,
                auth_prefix=prefix,
                auth_header=auth_header,
                default_headers=default_headers,
            )
            async for record in connector.extract_with_healing(
                method,
                effective_path,
                json_body=json_body,
                pagination=pagination_override,
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

    auth_mode = "none (no credentials)" if no_auth else f"{prefix or 'raw'} token via {auth_header}"
    console.print(f"[dim]  source:      {provider}[/dim]")
    console.print(f"[dim]  url:         {url}[/dim]")
    console.print(f"[dim]  endpoint:    {effective_path}[/dim]")
    console.print(f"[dim]  auth:        {auth_mode}[/dim]")
    console.print(f"[dim]  healing:     {'skipped (no-auth source)' if no_auth else 'enabled — rotates formats on 401/403'}[/dim]")
    console.print(f"[dim]  checkpoint:  cursor saved every 100 records[/dim]")
    console.print(f"\n[cyan]Syncing {provider}{effective_path}...[/cyan]")
    try:
        with console.status("Extracting..."):
            total = asyncio.run(_run())
    except ConnectorError as e:
        console.print(f"\n[red]Sync failed:[/red] {e}")
        # If auth healing exhausted, show docs so the user can get fresh credentials
        if "Auth healing exhausted" in str(e) or "auth" in str(e).lower():
            _show_auth_docs(provider, context="healing")
        raise typer.Exit(1)
    except Exception as e:
        # Catch httpx.ConnectError and any other network-level failures
        err_type = type(e).__name__
        console.print(f"\n[red]Connection error ({err_type}):[/red] {e}")
        console.print(f"[dim]  Check that {url} is reachable[/dim]")
        raise typer.Exit(1)

    console.print(f"\n[green bold]Done:[/green bold] {total} records extracted")
    console.print(f"[dim]  output:     {output_file}[/dim]")
    console.print(f"[dim]  checkpoint: saved — next run resumes from cursor, not from zero[/dim]")


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
    message: Annotated[str | None, typer.Argument(help="Opening message (optional — omit to start interactive session)")] = None,
):
    """Interactive AI assistant for Zero to Pipeline.

    Start a conversation — ask anything about your data sources, run syncs,
    add new sources, or get help. The assistant understands context across
    the whole conversation.

    Examples:
      pipeline chat                          # start interactive session
      pipeline chat "add mlflow"             # one-shot message
    """
    from data_pipeline.connectors.llm_discovery import get_llm_provider
    from rich.markdown import Markdown

    llm = get_llm_provider()
    if not llm:
        console.print("[red]LLM not available.[/red] Run: pipeline auth set <provider>")
        return

    source_store = SourceStore()

    def _context_snapshot() -> str:
        """Build a compact description of current pipeline state for the system prompt."""
        sources = source_store.list()
        if not sources:
            sources_str = "No sources configured yet."
        else:
            lines = [f"  - {s.provider} ({s.api.base_url if s.api else 'no url'}) [{s.connection_status.value}]"
                     for s in sources]
            sources_str = "\n".join(lines)
        known = ", ".join(provider_registry.known_providers)
        return f"Configured sources:\n{sources_str}\n\nKnown provider presets: {known}"

    SYSTEM = """\
You are the Zero-to-Pipeline assistant — an expert in connecting to APIs and running data pipelines.

You help users:
- Add data sources (pipeline source add <provider> [--base-url URL])
- Sync / extract data (pipeline sync run <provider>)
- Check authentication (pipeline auth set <provider> / pipeline auth status)
- Test connections (pipeline source test <provider>)
- List sources (pipeline source list)
- Run the full demo pipeline (python -m examples.demo_pipeline)

When a user asks you to do something, respond conversationally AND include a
JSON action block at the very end of your response when you want to execute a command.

Action block format (always last, always valid JSON):
{"action": "source_add", "provider": "mlflow", "base_url": null}
{"action": "sync_run", "provider": "mlflow"}
{"action": "source_list"}
{"action": "auth_status"}
{"action": "source_test", "provider": "mlflow"}
{"action": "none"}

Rules:
- Always be helpful, concise, and direct — like Claude or ChatGPT.
- Explain what you are doing and why.
- If you need more information (e.g. which source to sync), ask a follow-up question and use action "none".
- Never hallucinate commands. Only use the actions listed above.
- Keep your conversational response SHORT — 1-3 sentences before the action block.
- ALWAYS end with the action JSON block, even if action is "none".
"""

    def _execute_action(action: dict) -> str | None:
        """Execute an action dict and return a result string for context."""
        name = action.get("action", "none")
        if name == "none":
            return None
        if name == "source_list":
            source_list()
            return "Listed current sources."
        if name == "auth_status":
            auth_status()
            return "Showed auth status."
        if name == "source_add":
            provider = action.get("provider")
            if not provider:
                console.print("[yellow]No provider specified.[/yellow]")
                return "Provider name missing."
            try:
                source_add(provider=provider, base_url=action.get("base_url"), auth_type=None, force=False)
                return f"Added source: {provider}"
            except (SystemExit, typer.Exit):
                return f"Source add failed or already exists for: {provider}"
        if name == "sync_run":
            provider = action.get("provider")
            if not provider:
                console.print("[yellow]No provider specified.[/yellow]")
                return "Provider missing for sync."
            try:
                sync_run(provider=provider, path=None, base_url=None, full=False)
                return f"Sync complete for: {provider}"
            except (SystemExit, typer.Exit):
                return f"Sync failed for: {provider}"
            except Exception as e:
                console.print(f"[red]Sync error:[/red] {e}")
                return f"Sync error: {e}"
        if name == "source_test":
            provider = action.get("provider")
            if provider:
                try:
                    source_test(provider=provider)
                except (SystemExit, typer.Exit):
                    pass
                return f"Tested connection for: {provider}"
        return None

    history: list[dict] = []

    def _turn(user_msg: str) -> None:
        # Inject fresh context into system prompt each turn
        system_with_ctx = SYSTEM + f"\n\nCurrent pipeline state:\n{_context_snapshot()}"

        history.append({"role": "user", "content": user_msg})

        with console.status("[dim]Thinking...[/dim]", spinner="dots"):
            result = llm.chat(history, system=system_with_ctx, max_tokens=1024, timeout=45)

        if not result.success:
            console.print(f"[red]LLM error:[/red] {result.error}")
            history.pop()
            return

        raw = result.text

        # Split conversational text from the trailing JSON action block
        action: dict = {"action": "none"}
        display_text = raw

        # Find the last { ... } block
        last_brace = raw.rfind("{")
        if last_brace != -1:
            candidate = raw[last_brace:]
            # Check it closes on the same line or within a few chars
            end = candidate.find("}")
            if end != -1:
                json_str = candidate[:end + 1]
                try:
                    parsed = json.loads(json_str)
                    if "action" in parsed:
                        action = parsed
                        display_text = raw[:last_brace].strip()
                except json.JSONDecodeError:
                    pass

        # Print the conversational response
        if display_text:
            console.print()
            console.print(Markdown(display_text))

        # Execute the action
        action_result = _execute_action(action)

        # Add assistant turn to history (full raw response for context continuity)
        history.append({"role": "assistant", "content": raw})

        # Feed action result back as a hidden system note for next turn
        if action_result:
            history.append({"role": "user", "content": f"[system: action result — {action_result}]"})
            history.append({"role": "assistant", "content": "Got it."})

    # --- One-shot mode ---
    if message:
        _turn(message)
        return

    # --- Interactive REPL mode ---
    console.print("[bold cyan]Zero-to-Pipeline Assistant[/bold cyan]  [dim](type 'exit' or Ctrl-C to quit)[/dim]")
    console.print()

    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "bye", ":q"}:
            console.print("[dim]Goodbye.[/dim]")
            break

        _turn(user_input)


@app.command("doctor")
def doctor():
    """Run health checks on the pipeline system."""
    checks = [
        ("Credential store accessible", _check_credential_store),
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



def _check_credential_store():
    store = CredentialStore()
    store.retrieve("__health_check__")
    if store.backend_type == "file":
        console.print("  [dim]  (using encrypted file — no OS keychain detected)[/dim]")


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
    import sys

    # Suggestion map: wrong command → correct command
    # Keyed by the first arg(s) the user typed. Handles common mistakes.
    # Build dynamic suggestions that include the provider name when given
    raw_args = [a for a in sys.argv[1:] if not a.startswith("-")]
    # The provider is usually the last non-command word
    _provider = raw_args[-1] if raw_args and raw_args[-1] not in {
        "add", "connect", "run", "sync", "list", "ls", "show", "fetch",
        "pull", "extract", "login", "test", "check", "health", "sources",
        "remove", "delete", "register", "setup", "source", "auth", "status",
        "connect", "configure", "credentials", "diagnose", "token",
    } else "<provider>"

    SUGGESTIONS: dict[tuple, str] = {
        # top-level shorthand → correct namespaced command
        ("add",):         f"pipeline source add {_provider}",
        ("connect",):     f"pipeline source add {_provider}",
        ("register",):    f"pipeline source add {_provider}",
        ("setup",):       f"pipeline source add {_provider}",
        ("configure",):   f"pipeline source add {_provider}",
        ("list",):        "pipeline source list",
        ("ls",):          "pipeline source list",
        ("show",):        "pipeline source list",
        ("sources",):     "pipeline source list",
        ("run",):         f"pipeline sync run {_provider}",
        ("extract",):     f"pipeline sync run {_provider}",
        ("fetch",):       f"pipeline sync run {_provider}",
        ("pull",):        f"pipeline sync run {_provider}",
        ("test",):        f"pipeline source test {_provider}",
        ("check",):       f"pipeline source test {_provider}",
        ("remove",):      f"pipeline source remove {_provider}",
        ("delete",):      f"pipeline source remove {_provider}",
        ("login",):       f"pipeline auth set {_provider}",
        ("token",):       f"pipeline auth set {_provider}",
        ("credentials",): "pipeline auth status",
        ("status",):      "pipeline sync status  OR  pipeline auth status",
        ("health",):      "pipeline doctor",
        ("diagnose",):    "pipeline doctor",
        # wrong subcommand structure — "sync <provider>" instead of "sync run <provider>"
        ("sync",):        f"pipeline sync run {_provider}",
        # "auth <provider>" instead of "auth set <provider>"
        ("auth",):        f"pipeline auth set {_provider}",
        # wrong subcommand order inside groups
        ("source", "run"):   f"pipeline sync run {_provider}",
        ("source", "sync"):  f"pipeline sync run {_provider}",
        ("source", "fetch"): f"pipeline sync run {_provider}",
    }

    # Capture args before app() may mutate sys.argv
    _captured_args = [a for a in sys.argv[1:] if not a.startswith("-")]

    # Intercept Typer's click.UsageError before it becomes a SystemExit,
    # so we can show a clean suggestion with no traceback.
    try:
        app(standalone_mode=False)
    except ClickUsageError:
        args = _captured_args
        key2 = tuple(args[:2])
        key1 = tuple(args[:1])

        suggestion = SUGGESTIONS.get(key2) or SUGGESTIONS.get(key1)

        console = Console()
        # Show only what's useful — no error message, no traceback
        console.print(f"[red]Unknown command:[/red] pipeline {' '.join(args)}")
        if suggestion:
            console.print(f"\n[yellow]Did you mean?[/yellow]  [bold cyan]{suggestion}[/bold cyan]")
        else:
            console.print("\n[yellow]Common commands:[/yellow]")
            console.print("  [cyan]pipeline source add <provider>[/cyan]     — add any data source")
            console.print("  [cyan]pipeline auth set <provider>[/cyan]       — store credentials")
            console.print("  [cyan]pipeline sync run <provider>[/cyan]       — extract data")
            console.print("  [cyan]pipeline source list[/cyan]               — list configured sources")
            console.print("  [cyan]pipeline doctor[/cyan]                    — health check")
            console.print("  [cyan]pipeline chat[/cyan]                      — interactive assistant")
        console.print(f"\n[dim]Run 'pipeline --help' for all commands.[/dim]")
        sys.exit(2)
    except SystemExit:
        raise
    except Exception:
        raise


if __name__ == "__main__":
    main()
