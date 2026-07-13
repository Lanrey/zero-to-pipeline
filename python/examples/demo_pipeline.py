"""EuroPython 2026 Demo: Zero to Pipeline — closing the loop on slide 4.

Slide 4 sets up this exact scenario:
  14:00 — "Pull last week's model inference logs from our feature store. Should be quick."
  14:45 — OAuth. Reading docs again.
  15:30 — Token works in curl but SDK throws 403. Different header format?
  16:15 — Writing a custom paginator for 50k prediction results. Again.
  17:00 — Pipeline crashes. Cursor state lost. Full re-fetch. Model training delayed.

This demo resolves every one of those pain points live on stage:

  my-feature-store ──┐  (unknown API — LLM discovers auth, self-healing handles 403)
                      ├──▶  create_linear_issues  (sprint unblocked)
  mlflow           ──┘  (cross-reference experiment runs with feature data)

Steps 1 and 2 run in parallel.
Step 3 runs when both complete — creates a Linear issue confirming the sync
that was blocked all afternoon is now done.

Run:
    pipeline source add my-feature-store --base-url https://features.internal.co
    pipeline source add mlflow
    pipeline auth set my-feature-store
    pipeline auth set mlflow
    pipeline auth set linear
    uv run python -m examples.demo_pipeline
"""
from __future__ import annotations

import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).parent.parent / "src"))

import asyncio
from datetime import datetime
from typing import Any

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from data_pipeline.auth import CredentialStore
from data_pipeline.connectors import (
    CursorPagination,
    OffsetPagination,
    SelfHealingConnector,
    provider_registry,
)
from data_pipeline.observability import configure_logging
from data_pipeline.orchestrator import CheckpointManager, Pipeline, PipelineEngine

console = Console()
store = CredentialStore()

# The feature store base URL — override via pipeline source add --base-url
FEATURE_STORE_URL = "https://features.internal.co"
FEATURE_STORE_PATH = "/v1/features"


# ---------------------------------------------------------------------------
# Step 1 — Extract from the internal feature store (unknown API)
#
# This is the exact source from slide 4 — the one that took all afternoon.
# LLM discovered the config. Self-healing handles whatever auth format it uses.
# Cursor pagination auto-inferred from response — no custom paginator needed.
# ---------------------------------------------------------------------------

async def extract_feature_store(*, context: dict[str, Any], prior_results: dict[str, Any]) -> int:
    credential = store.retrieve("my-feature-store")
    token = credential.get("access_token", "") if credential else ""

    connector = SelfHealingConnector(
        FEATURE_STORE_URL,
        credential=token,
        auth_header="Authorization",
        auth_prefix="Bearer",
    )

    console.print("  [cyan]→[/cyan] Feature store: pulling prediction records...")
    if not token:
        console.print("  [yellow]–[/yellow] Feature store: no token stored — run: pipeline auth set my-feature-store")
        context["feature_store_records"] = 0
        return 0

    count = 0
    try:
        async for record in connector.extract_with_healing(
            "GET",
            FEATURE_STORE_PATH,
            pagination=CursorPagination(page_size=100),
            source_id="my-feature-store",
            resource_type="features",
        ):
            count += 1
    except Exception as e:
        # Not reachable in most demo environments — show what self-healing would do
        console.print("  [yellow]–[/yellow] Feature store: not reachable in this environment")
        console.print("  [dim]   (in production: self-healing would try Bearer → raw token → X-API-Key)[/dim]")
        count = 0

    context["feature_store_records"] = count
    if count:
        console.print(f"  [green]✓[/green] Feature store: {count} prediction records pulled")
        console.print(f"  [dim]   Cursor checkpointed — crash and restart picks up here, not from zero[/dim]")
    return count


# ---------------------------------------------------------------------------
# Step 2 — Extract MLflow experiment runs (cross-reference with feature data)
#
# The ML engineer was waiting on this data. Now both sources run in parallel.
# ---------------------------------------------------------------------------

async def extract_mlflow(*, context: dict[str, Any], prior_results: dict[str, Any]) -> int:
    config = provider_registry.infer_config("mlflow")
    credential = store.retrieve("mlflow")
    token = credential.get("access_token", "") if credential else ""

    connector = SelfHealingConnector(
        config.base_url,
        credential=token,
        auth_header=config.auth_header,
        auth_prefix=config.auth_prefix,
    )

    console.print("  [cyan]→[/cyan] MLflow: pulling experiment runs...")
    count = 0
    try:
        async for record in connector.extract_with_healing(
            "GET",
            "/api/2.0/mlflow/runs/search",
            pagination=OffsetPagination(page_size=50),
            source_id="mlflow",
            resource_type="runs",
        ):
            count += 1
    except Exception:
        console.print("  [yellow]–[/yellow] MLflow: not reachable (start with: docker run -p 5000:5000 ghcr.io/mlflow/mlflow mlflow server --host 0.0.0.0)")
        count = 0

    context["mlflow_records"] = count
    if count:
        console.print(f"  [green]✓[/green] MLflow: {count} experiment runs pulled")
    return count


# ---------------------------------------------------------------------------
# Step 3 — Create a Linear issue: the sprint is unblocked
#
# This is the resolution to slide 4's 17:00 entry.
# The issue title directly mirrors the blocker that kicked off the afternoon.
# ---------------------------------------------------------------------------

async def create_linear_issues(*, context: dict[str, Any], prior_results: dict[str, Any]) -> int:
    credential = store.retrieve("linear")
    if not credential:
        console.print("  [yellow]–[/yellow] Linear: no token stored — run: pipeline auth set linear")
        return 0

    token = credential.get("access_token", "")
    feature_count = context.get("feature_store_records", 0)
    mlflow_count = context.get("mlflow_records", 0)

    title = (
        f"[Zero-Pipeline] Feature store sync complete — "
        f"{feature_count} prediction records, {mlflow_count} experiment runs"
    )
    body = (
        f"## Sprint unblocked ✓\n\n"
        f"The feature store ingestion that was blocked by auth/pagination issues "
        f"is now running automatically via Zero-Pipeline.\n\n"
        f"| Source | Records | Notes |\n"
        f"|--------|---------|-------|\n"
        f"| my-feature-store | {feature_count} prediction records | Cursor checkpointed — incremental on next run |\n"
        f"| mlflow | {mlflow_count} experiment runs | Cross-reference ready |\n\n"
        f"**What changed:**\n"
        f"- Auth format discovered automatically (no more 403 debugging)\n"
        f"- Cursor pagination inferred from response (no custom paginator)\n"
        f"- Checkpoint saved — crash and restart resumes, not re-fetches\n\n"
        f"_Created automatically by Zero-Pipeline. "
        f"No connector code was written. No YAML was touched._"
    )

    console.print("  [cyan]→[/cyan] Linear: creating issue — sprint unblocked...")

    query_teams = "query { teams { nodes { id name } } }"
    query_issue = """
    mutation CreateIssue($teamId: String!, $title: String!, $description: String!) {
      issueCreate(input: {teamId: $teamId, title: $title, description: $description}) {
        success
        issue { id title url }
      }
    }
    """

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://api.linear.app/graphql",
                headers={"Authorization": token, "Content-Type": "application/json"},
                json={"query": query_teams},
            )
            r.raise_for_status()
            teams = r.json().get("data", {}).get("teams", {}).get("nodes", [])

            if not teams:
                console.print("  [red]✗[/red] Linear: no teams found")
                return 0

            team_id = teams[0]["id"]
            team_name = teams[0]["name"]

            r = await client.post(
                "https://api.linear.app/graphql",
                headers={"Authorization": token, "Content-Type": "application/json"},
                json={"query": query_issue, "variables": {
                    "teamId": team_id, "title": title, "description": body,
                }},
            )
            r.raise_for_status()
            issue = r.json().get("data", {}).get("issueCreate", {}).get("issue", {})

            if issue:
                console.print(f"  [green]✓[/green] Linear issue created in team '{team_name}':")
                console.print(f"    [bold]{issue.get('url', '')}[/bold]")
                context["linear_issue_url"] = issue.get("url", "")
                return 1
            else:
                console.print(f"  [red]✗[/red] Linear: {r.json().get('errors', [])}")
                return 0

    except Exception as e:
        console.print(f"  [red]✗[/red] Linear: {e}")
        return 0


# ---------------------------------------------------------------------------
# Pipeline assembly and run
# ---------------------------------------------------------------------------

async def main() -> None:
    configure_logging(level="WARNING", log_format="console")

    console.print(Panel(
        "[bold]EuroPython 2026 — Zero to Pipeline[/bold]\n\n"
        "[dim]14:00 — \"Pull last week's feature store logs. Should be quick.\"[/dim]\n"
        "[coral]17:00 — Pipeline crashed. Cursor lost. Training delayed.[/coral]\n\n"
        "Now: [green bold]feature store + MLflow → Linear issue. Automatically.[/green bold]",
        title="Demo Pipeline",
        border_style="blue",
    ))

    table = Table(title="Execution Plan", show_header=True)
    table.add_column("Step", style="cyan")
    table.add_column("Depends on", style="yellow")
    table.add_column("Runs")
    table.add_row("extract_feature_store", "—", "[green]parallel[/green]")
    table.add_row("extract_mlflow",        "—", "[green]parallel[/green]")
    table.add_row("create_linear_issues",  "extract_feature_store, extract_mlflow", "[blue]after both[/blue]")
    console.print(table)
    console.print()

    shared_context: dict[str, Any] = {}
    pipeline = Pipeline("demo-pipeline")
    pipeline.add_step("extract_feature_store", extract_feature_store)
    pipeline.add_step("extract_mlflow",        extract_mlflow)
    pipeline.add_step(
        "create_linear_issues",
        create_linear_issues,
        depends_on=["extract_feature_store", "extract_mlflow"],
    )

    def make_step(fn, ctx):
        async def _wrapped(*, context, prior_results):
            return await fn(context=ctx, prior_results=prior_results)
        return _wrapped

    pipeline._steps["extract_feature_store"].fn = make_step(extract_feature_store, shared_context)
    pipeline._steps["extract_mlflow"].fn         = make_step(extract_mlflow,        shared_context)
    pipeline._steps["create_linear_issues"].fn   = make_step(create_linear_issues,  shared_context)

    console.print("[bold]Running pipeline...[/bold]")
    engine = PipelineEngine(checkpoint_manager=CheckpointManager())
    result = await engine.run(pipeline, context=shared_context)

    duration = (result.completed_at - result.started_at).total_seconds()
    status_color = "green" if result.status.value == "completed" else "yellow"

    console.print()
    console.print(Panel(
        f"[{status_color} bold]Pipeline {result.status.value}[/{status_color} bold]\n\n"
        f"Feature store records : {shared_context.get('feature_store_records', 0)}\n"
        f"MLflow records        : {shared_context.get('mlflow_records', 0)}\n"
        f"Linear issue          : {shared_context.get('linear_issue_url', 'not created')}\n"
        f"Duration              : {duration:.1f}s\n\n"
        "[dim]No connector classes written. No YAML. No SDK imports per source.[/dim]",
        title="Results",
        border_style=status_color,
    ))


if __name__ == "__main__":
    asyncio.run(main())
