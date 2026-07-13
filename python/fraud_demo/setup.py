"""Start all local services needed for the fraud detection demo.

Run this once before the demo. Press Ctrl-C to stop all services.

Services started:
  MLflow      → http://127.0.0.1:5001  (experiment tracking, no auth)
  Feast       → http://127.0.0.1:6566  (feature server)
  Prometheus  → already running via Docker (http://localhost:9090)
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import httpx
from rich.console import Console
from rich.table import Table

console = Console()

BASE = Path(__file__).parent
REPO = BASE / "feature_repo"


def wait_for(url: str, name: str, timeout: int = 30) -> bool:
    """Poll url until it responds or timeout."""
    for i in range(timeout):
        try:
            r = httpx.get(url, timeout=2)
            if r.status_code < 500:
                return True
        except Exception:
            pass
        time.sleep(1)
        if i % 5 == 4:
            console.print(f"  [dim]  still waiting for {name}...[/dim]")
    return False


def main():
    console.print("\n[bold]Zero-to-Pipeline — Fraud Detection Demo Setup[/bold]\n")

    # ── Generate data if needed ──────────────────────────────────────────────
    if not (BASE / "data" / "transactions.parquet").exists():
        console.print("[cyan]→[/cyan] Generating synthetic fraud dataset...")
        subprocess.run([sys.executable, str(BASE / "generate_data.py")], check=True)
    else:
        console.print("[dim]  ✓ Dataset already generated[/dim]")

    # ── Feast: apply + materialize ───────────────────────────────────────────
    console.print("[cyan]→[/cyan] Feast: applying feature definitions...")
    subprocess.run(
        [sys.executable, "-m", "feast", "-c", str(REPO), "apply"],
        capture_output=True,
    )
    console.print("[cyan]→[/cyan] Feast: materializing features to online store...")
    from datetime import datetime, timezone
    end_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    subprocess.run(
        [sys.executable, "-m", "feast", "-c", str(REPO), "materialize-incremental", end_ts],
        capture_output=True,
    )
    console.print("[dim]  ✓ Feast features materialized[/dim]")

    procs = []

    # ── MLflow on 5001 (no auth) ─────────────────────────────────────────────
    import httpx as _httpx
    try:
        r = _httpx.get("http://127.0.0.1:5001/api/2.0/mlflow/experiments/search",
                       timeout=2)
        if r.status_code == 200:
            console.print("[dim]  ✓ MLflow already running on 5001[/dim]")
        else:
            raise Exception("not ready")
    except Exception:
        console.print("[cyan]→[/cyan] Starting MLflow on port 5001...")
        p = subprocess.Popen(
            [sys.executable, "-m", "mlflow", "server",
             "--host", "127.0.0.1", "--port", "5001",
             "--backend-store-uri", "sqlite:////tmp/mlflow_demo.db",
             "--no-serve-artifacts"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        procs.append(("MLflow", p))
        if wait_for("http://127.0.0.1:5001/api/2.0/mlflow/experiments/search", "MLflow"):
            console.print("[green]  ✓ MLflow ready at http://127.0.0.1:5001[/green]")
        else:
            console.print("[red]  ✗ MLflow failed to start[/red]")

    # ── Feast feature server on 6566 ────────────────────────────────────────
    try:
        r = _httpx.get("http://127.0.0.1:6566/health", timeout=2)
        console.print("[dim]  ✓ Feast server already running on 6566[/dim]")
    except Exception:
        console.print("[cyan]→[/cyan] Starting Feast feature server on port 6566...")
        p = subprocess.Popen(
            [sys.executable, "-m", "feast", "-c", str(REPO), "serve",
             "--host", "127.0.0.1", "--port", "6566"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        procs.append(("Feast", p))
        if wait_for("http://127.0.0.1:6566/health", "Feast"):
            console.print("[green]  ✓ Feast ready at http://127.0.0.1:6566[/green]")
        else:
            console.print("[yellow]  ⚠ Feast server slow to start — will use parquet fallback[/yellow]")

    # ── Prometheus check ─────────────────────────────────────────────────────
    try:
        r = _httpx.get("http://localhost:9090/-/healthy", timeout=2)
        console.print("[dim]  ✓ Prometheus running on 9090[/dim]")
    except Exception:
        console.print("[yellow]  ⚠ Prometheus not running — start with:[/yellow]")
        console.print("    docker run -d -p 9090:9090 prom/prometheus")

    # ── Summary ──────────────────────────────────────────────────────────────
    console.print()
    table = Table(title="Demo Services", show_header=True)
    table.add_column("Service", style="cyan")
    table.add_column("URL")
    table.add_column("Purpose")
    table.add_row("Feast",      "http://127.0.0.1:6566", "Feature vectors for fraud model")
    table.add_row("MLflow",     "http://127.0.0.1:5001", "Experiment tracking & model runs")
    table.add_row("Prometheus", "http://localhost:9090",  "Model metrics & monitoring")
    table.add_row("Linear",     "https://linear.app",     "Issue tracker (cloud)")
    console.print(table)

    console.print("\n[green bold]Setup complete.[/green bold] Run the demo pipeline:")
    console.print("  [bold]uv run python -m fraud_demo.run_pipeline[/bold]")
    console.print("\n  Or press [bold]Cmd+Ctrl+0[/bold] in Ghostty\n")

    if procs:
        console.print("[dim]Press Ctrl-C to stop all services[/dim]")
        try:
            for _, p in procs:
                p.wait()
        except KeyboardInterrupt:
            console.print("\n[dim]Stopping services...[/dim]")
            for name, p in procs:
                p.terminate()
                console.print(f"  [dim]Stopped {name}[/dim]")


if __name__ == "__main__":
    main()
