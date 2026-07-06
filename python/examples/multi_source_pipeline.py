"""Multi-source MLOps pipeline: experiment tracker + orchestrator + monitoring → JSONL.

A concrete Data Engineering & MLOps use case:
- MLflow: pull experiment runs and model metrics
- Airflow: pull DAG run history and task statuses
- Prometheus: pull model serving metrics

Demonstrates zero-per-provider code — the universal connector handles all three
with self-healing, checkpointing, and parallel extraction.

Run:
    python -m examples.multi_source_pipeline
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from data_pipeline.auth import CredentialStore
from data_pipeline.connectors import (
    OffsetPagination,
    SelfHealingConnector,
    provider_registry,
)
from data_pipeline.observability import configure_logging
from data_pipeline.observability.metrics import metrics
from data_pipeline.orchestrator import CheckpointManager, Pipeline, PipelineEngine

console = Console()
OUTPUT_DIR = Path("./pipeline_output")


async def build_mlops_pipeline() -> None:
    """Build and run a pipeline ingesting from multiple MLOps data sources."""
    configure_logging(level="WARNING", log_format="console")

    console.print(Panel(
        "[bold]MLOps Data Pipeline[/bold]\n"
        "MLflow (runs) + Airflow (dag_runs) + Prometheus (metrics) → JSONL\n\n"
        "[dim]No per-provider connector code — universal API connector + self-healing[/dim]",
        title="Zero to Pipeline Demo",
        border_style="blue",
    ))

    # Show source discovery — no hardcoded connectors
    mlops_sources = ["mlflow", "airflow", "prometheus", "wandb", "prefect"]

    table = Table(title="Source Discovery (all auto-configured)")
    table.add_column("Provider", style="cyan")
    table.add_column("Base URL", style="green")
    table.add_column("Source", style="yellow")
    table.add_column("Pagination", style="blue")

    for provider in mlops_sources:
        config = provider_registry.infer_config(provider)
        table.add_row(
            provider,
            config.base_url,
            config.source,
            config.pagination_style,
        )

    console.print(table)
    console.print()

    # Build pipeline with dynamic steps
    checkpoint_mgr = CheckpointManager()
    engine = PipelineEngine(checkpoint_manager=checkpoint_mgr)
    pipeline = Pipeline("mlops-ingest")
    store = CredentialStore()

    async def extract_generic(
        *, context, prior_results, provider: str, path: str, pagination=None
    ) -> int:
        """Generic extraction step — works for ANY API."""
        config = provider_registry.infer_config(provider)
        credential = store.retrieve(provider)

        token = credential["access_token"] if credential else ""
        connector = SelfHealingConnector(
            config.base_url,
            token,
            auth_prefix=config.auth_prefix,
            auth_header=config.auth_header,
            default_headers=config.default_headers,
        )

        count = 0
        method = "POST" if config.api_style == "graphql" else "GET"
        async for _record in connector.extract_with_healing(
            method,
            path,
            source_id=provider,
            resource_type=path.strip("/").split("/")[-1],
            pagination=pagination,
        ):
            count += 1

        metrics.increment("records_extracted", count, provider=provider)
        if count > 0:
            console.print(f"  [green]✓[/green] {provider}: {count} records")
        else:
            console.print(f"  [yellow]–[/yellow] {provider}: no credentials stored, skipped")
        return count

    # Three sources — all run in parallel (no sequential bottleneck)
    async def extract_mlflow(*, context, prior_results):
        return await extract_generic(
            context=context, prior_results=prior_results,
            provider="mlflow", path="/api/2.0/mlflow/runs/search",
            pagination=OffsetPagination(page_size=100),
        )

    async def extract_airflow(*, context, prior_results):
        return await extract_generic(
            context=context, prior_results=prior_results,
            provider="airflow", path="/api/v1/dags/~/dagRuns",
            pagination=OffsetPagination(page_size=100),
        )

    async def extract_prometheus(*, context, prior_results):
        return await extract_generic(
            context=context, prior_results=prior_results,
            provider="prometheus", path="/api/v1/query",
        )

    pipeline.add_step("extract_mlflow",     extract_mlflow)
    pipeline.add_step("extract_airflow",    extract_airflow)
    pipeline.add_step("extract_prometheus", extract_prometheus)

    # Show execution plan
    console.print("[bold]Execution Plan:[/bold]")
    for i, layer in enumerate(pipeline.execution_order()):
        step_names = [s.name for s in layer]
        console.print(f"  Layer {i + 1} (parallel): {', '.join(step_names)}")

    console.print("\n[dim]All sources extract in parallel — no sequential bottleneck[/dim]\n")

    result = await engine.run(
        pipeline,
        context={"source_id": "mlops-multi", "started_at": datetime.now().isoformat()},
    )

    if result.status.value == "completed":
        console.print(Panel(
            f"[green bold]Pipeline completed![/green bold]\n\n"
            f"Total records:  {result.total_records}\n"
            f"Steps:          {len(result.steps)} (ran in parallel)\n"
            f"Duration:       {(result.completed_at - result.started_at).total_seconds():.1f}s\n"
            f"Output:         {OUTPUT_DIR.absolute()}",
            title="Results",
            border_style="green",
        ))
    else:
        console.print(Panel(
            f"[yellow bold]Pipeline completed[/yellow bold]\n\n"
            f"Status: {result.status.value}\n"
            f"Note: Sources without stored credentials are skipped gracefully.\n"
            f"Run: pipeline auth set mlflow  (or airflow/prometheus)",
            title="Results",
            border_style="yellow",
        ))


if __name__ == "__main__":
    asyncio.run(build_mlops_pipeline())
