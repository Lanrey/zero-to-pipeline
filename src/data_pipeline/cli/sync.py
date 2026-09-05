"""Sync CLI commands."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import typer
from rich.table import Table

from data_pipeline.cli.helpers import (
    console,
    resolve_for_provider,
    resolve_request_params,
    show_auth_docs,
)
from data_pipeline.connectors import (
    ConnectorError,
    MlflowRunsPagination,
    SelfHealingConnector,
    provider_registry,
)
from data_pipeline.schemas import CheckpointState, ConnectionStatus
from data_pipeline.sources import SourceStore

sync_app = typer.Typer(help="Run data syncs")


@sync_app.command("run")
def sync_run(
    provider: str = typer.Argument(help="Provider to sync from"),
    path: str | None = typer.Argument(None, help="API endpoint path (optional if source has default)"),
    base_url: str | None = typer.Option(None, "--base-url", help="Override base URL"),
    full: bool = typer.Option(False, "--full", help="Force full sync (ignore checkpoints)"),
):
    """Run a data extraction from any API endpoint with self-healing."""
    from data_pipeline.orchestrator import CheckpointManager

    rc = resolve_for_provider(provider, base_url=base_url)
    config = provider_registry.infer_config(provider)

    effective_path = path or _resolve_default_path(provider)
    if not effective_path:
        console.print(f"[red]No endpoint path specified and no default for '{provider}'.[/red]")
        console.print("Specify a path: pipeline sync run <provider> <path>")
        config = provider_registry.infer_config(provider)
        if config.default_endpoints:
            console.print(f"Available endpoints: {list(config.default_endpoints.keys())}")
        raise typer.Exit(1)

    prefix = "" if rc.no_auth else rc.auth_prefix

    checkpoint_mgr = CheckpointManager()
    pipeline_id = "sync"
    source_id = f"{provider}_{effective_path}".replace("/", "_").replace(":", "_")
    existing_checkpoint: CheckpointState | None = None

    if full:
        checkpoint_mgr.clear(pipeline_id, source_id)
        console.print("[cyan]Full sync mode: checkpoint cleared[/cyan]")
    else:
        existing_checkpoint = checkpoint_mgr.load(pipeline_id, source_id)
        if existing_checkpoint and existing_checkpoint.cursor:
            console.print(f"[cyan]Resuming from checkpoint cursor: {existing_checkpoint.cursor}[/cyan]")

    output_dir = Path.home() / ".zero-pipeline" / "output" / provider
    output_dir.mkdir(parents=True, exist_ok=True)
    resource_name = effective_path.strip("/").split("/")[-1].replace("/", "_")
    output_file = output_dir / f"{resource_name}.jsonl"

    method, json_body = resolve_request_params(config, effective_path)

    pagination_override = None
    if "mlflow/runs/search" in effective_path:
        pagination_override = MlflowRunsPagination(page_size=5)

    async def _run():
        count = 0
        last_cursor: str | None = None
        cp = existing_checkpoint

        with output_file.open("a", encoding="utf-8") as f:
            connector = SelfHealingConnector(
                rc.url, rc.token,
                auth_prefix=prefix,
                auth_header=rc.auth_header,
                default_headers=rc.default_headers,
            )
            async for record in connector.extract_with_healing(
                method, effective_path,
                json_body=json_body,
                pagination=pagination_override,
                source_id=provider,
                resource_type=resource_name,
                checkpoint=cp,
            ):
                count += 1
                last_cursor = record.cursor
                f.write(json.dumps(record.model_dump(mode="json"), default=str) + "\n")

                if count <= 3:
                    record_id = record.raw_data.get("id") or record.raw_data.get("title") or record.id
                    console.print(f"  [dim]{count}.[/dim] {record_id}")
                elif count == 4:
                    console.print("  ...")

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

        if last_cursor:
            checkpoint_mgr.save(CheckpointState(
                pipeline_id=pipeline_id,
                source_id=source_id,
                cursor=last_cursor,
                last_sync_at=datetime.now(),
            ))

        source_store = SourceStore()
        source_store.update_status(
            provider.lower(), ConnectionStatus.CONNECTED, last_sync_at=datetime.now(),
        )
        return count

    auth_mode = "none (no credentials)" if rc.no_auth else f"{prefix or 'raw'} token via {rc.auth_header}"
    console.print(f"[dim]  source:      {provider}[/dim]")
    console.print(f"[dim]  url:         {rc.url}[/dim]")
    console.print(f"[dim]  endpoint:    {effective_path}[/dim]")
    console.print(f"[dim]  auth:        {auth_mode}[/dim]")
    healing_mode = "skipped (no-auth source)" if rc.no_auth else "enabled \u2014 rotates formats on 401/403"
    console.print(f"[dim]  healing:     {healing_mode}[/dim]")
    console.print("[dim]  checkpoint:  cursor saved every 100 records[/dim]")
    console.print(f"\n[cyan]Syncing {provider}{effective_path}...[/cyan]")

    try:
        with console.status("Extracting..."):
            total = asyncio.run(_run())
    except ConnectorError as e:
        console.print(f"\n[red]Sync failed:[/red] {e}")
        if "Auth healing exhausted" in str(e) or "auth" in str(e).lower():
            show_auth_docs(provider, context="healing")
        raise typer.Exit(1) from None
    except Exception as e:
        err_type = type(e).__name__
        console.print(f"\n[red]Connection error ({err_type}):[/red] {e}")
        console.print(f"[dim]  Check that {rc.url} is reachable[/dim]")
        raise typer.Exit(1) from None

    console.print(f"\n[green bold]Done:[/green bold] {total} records extracted")
    console.print(f"[dim]  output:     {output_file}[/dim]")
    console.print("[dim]  checkpoint: saved \u2014 next run resumes from cursor, not from zero[/dim]")


def _resolve_default_path(provider: str) -> str | None:
    """Resolve the default endpoint path from persisted source or registry."""
    source_store = SourceStore()
    persisted = source_store.get(provider.lower())
    if persisted and persisted.default_endpoint:
        return persisted.default_endpoint
    config = provider_registry.infer_config(provider)
    if config.default_endpoints:
        return next(iter(config.default_endpoints.values()), None)
    return None


@sync_app.command("status")
def sync_status():
    """Show sync status and checkpoint info."""
    from data_pipeline.orchestrator import CheckpointManager

    mgr = CheckpointManager()
    console.print("[dim]Checkpoint directory:[/dim]", str(mgr._dir))

    if mgr._dir.exists():
        checkpoints = list(mgr._dir.glob("*.json"))
        if checkpoints:
            table = Table(title="Active Checkpoints")
            table.add_column("Source", style="cyan")
            table.add_column("Cursor", style="green")
            table.add_column("Last Sync", style="dim")

            for cp_file in checkpoints:
                try:
                    data = json.loads(cp_file.read_text())
                    source_id = data.get("source_id", cp_file.stem)
                    cursor = data.get("cursor", "\u2014")
                    last_sync = str(data.get("last_sync_at", "\u2014"))[:19]
                    table.add_row(source_id, str(cursor)[:40], last_sync)
                except Exception:
                    pass

            console.print(table)
        else:
            console.print("[dim]No active checkpoints.[/dim]")
