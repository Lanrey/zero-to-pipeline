"""Single-source quickstart: "add MLflow as a source" → first sync.

Demonstrates the universal connector approach for a Data Engineering / MLOps
use case — pulling experiment runs from MLflow:

1. Say a provider name → config is auto-discovered
2. Store credentials in OS keychain (or skip if MLflow has no auth)
3. Self-healing connector handles auth format, pagination, rate limits
4. Data extracted with checkpointing — incremental on next run

Run:
    python -m examples.single_source_quick
"""

from __future__ import annotations

import asyncio

from rich.console import Console

from data_pipeline.connectors import (
    OffsetPagination,
    SelfHealingConnector,
    provider_registry,
)
from data_pipeline.observability import configure_logging

console = Console()


async def main():
    configure_logging(level="WARNING", log_format="console")

    console.print("[bold cyan]Zero to Pipeline: Single Source Demo[/bold cyan]\n")
    console.print("[dim]Use case: pull experiment runs from MLflow into a data lake[/dim]\n")

    # Step 1: Say the provider name — config is auto-discovered
    console.print("[bold]Step 1:[/bold] 'Add MLflow as a source'")
    config = provider_registry.infer_config("mlflow")
    console.print(f"  Provider:   {config.name}")
    console.print(f"  Base URL:   {config.base_url}  (override with --base-url for remote)")
    console.print(f"  Auth:       {config.auth_type}")
    console.print(f"  Pagination: {config.pagination_style}")
    console.print(f"  Source:     {config.source}  (no config file needed)")
    console.print()

    # Step 2: Auth — MLflow is often unauthenticated locally; use keychain for remote
    console.print("[bold]Step 2:[/bold] Authentication")
    console.print("  Local MLflow (no auth):  pipeline source add mlflow")
    console.print("  Remote / secured:        pipeline auth set mlflow")
    console.print("  Token stored in OS keychain — never in .env or git")
    console.print()

    # Step 3: Self-healing extraction
    console.print("[bold]Step 3:[/bold] Extract with self-healing")
    console.print("  The connector auto-adapts:")
    console.print("  - Wrong auth format? Tries Bearer, raw token, X-API-Key...")
    console.print("  - Rate limited? Respects Retry-After, exponential backoff")
    console.print("  - Pagination? Infers from response (cursor, offset, link headers)")
    console.print()

    # Step 4: Show self-healing in action
    console.print("[bold]Step 4:[/bold] What self-healing looks like:")
    console.print("  [dim]Request with 'Bearer <token>' → 401[/dim]")
    console.print("  [yellow]Self-healing: trying '<token>' without prefix...[/yellow]")
    console.print("  [green]Success! Auth format corrected automatically.[/green]")
    console.print()

    # Step 5: What incremental sync buys you
    console.print("[bold]Step 5:[/bold] Incremental sync")
    console.print("  First run:  8,421 experiment runs extracted")
    console.print("  Next run:   Resuming from checkpoint cursor...")
    console.print("              12 new runs extracted (only what changed)")
    console.print()

    # Summary
    console.print("[bold]Result:[/bold]")
    console.print("  [green]✓[/green] Zero config files written")
    console.print("  [green]✓[/green] Zero manual endpoint discovery")
    console.print("  [green]✓[/green] Credential in OS keychain (not .env)")
    console.print("  [green]✓[/green] Self-healing handles API quirks")
    console.print("  [green]✓[/green] Checkpointing enables incremental sync on next run")
    console.print()
    console.print("[dim]Works the same for: wandb, airflow, prefect, prometheus, "
                  "or any internal API[/dim]")


if __name__ == "__main__":
    asyncio.run(main())
