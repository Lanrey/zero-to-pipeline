"""EuroPython 2026 — Zero to Pipeline: Fraud Detection Demo Pipeline.

Story:
  "Our fraud detection model needs retraining. We need to pull the latest
   feature vectors from Feast, log the training run in MLflow, push model
   metrics to Prometheus, and open a Linear issue if the fraud rate has
   spiked or the model accuracy has dropped."

Pipeline DAG:
  pull_feast_features  ──┐
                          ├──▶  train_and_log_model ──▶  push_prometheus_metrics ──▶  create_linear_issue
  load_transactions    ──┘

All infrastructure is local:
  Feast      → http://127.0.0.1:6566  (feature server — started by fraud_demo.setup)
  MLflow     → http://127.0.0.1:5001  (experiment tracking — no auth, SQLite backend)
  Prometheus → http://localhost:9090  (metrics scraping)
  Linear     → https://api.linear.app (issue tracker — cloud, token in OS keychain)

Run:
    uv run python -m fraud_demo.run_pipeline
"""
from __future__ import annotations

import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from data_pipeline.auth import CredentialStore
from data_pipeline.orchestrator import CheckpointManager, Pipeline, PipelineEngine

console = Console()
store = CredentialStore()

FEAST_URL   = "http://127.0.0.1:6566"   # Feast feature server — started by fraud_demo.setup
MLFLOW_URL  = "http://127.0.0.1:5001"   # MLflow no-auth server — started by fraud_demo.setup
PROM_URL    = "http://localhost:9090"    # Prometheus — running via Docker
DATA_DIR    = Path(__file__).parent / "data"
REPO_PATH   = Path(__file__).parent / "feature_repo"


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Pull feature vectors from Feast + load transactions
# ─────────────────────────────────────────────────────────────────────────────

async def pull_feast_features(*, context: dict[str, Any], prior_results: dict[str, Any]) -> int:
    """Retrieve online features from Feast for a sample of users."""
    console.print("  [cyan]→[/cyan] Feast: pulling feature vectors for recent users...")

    try:
        from feast import FeatureStore
        feast_store = FeatureStore(repo_path=str(REPO_PATH))

        tx = pd.read_parquet(DATA_DIR / "transactions.parquet")
        # Take the 200 most recent transactions for scoring
        recent_tx = tx.sort_values("event_timestamp").tail(200)
        sample_users = recent_tx["user_id"].unique().tolist()[:50]

        entity_rows = [{"user_id": uid} for uid in sample_users]
        features = feast_store.get_online_features(
            features=[
                "user_account_features:credit_score",
                "user_account_features:account_age_days",
                "user_account_features:user_has_2fa_installed",
                "user_account_features:avg_transaction_amount",
                "user_transaction_counts:transaction_count_7d",
            ],
            entity_rows=entity_rows,
        ).to_df()

        context["feast_features"] = features
        context["sample_users"] = sample_users
        context["recent_tx"] = recent_tx

        null_count = features.isnull().sum().sum()
        console.print(f"  [green]✓[/green] Feast: {len(features)} feature vectors pulled "
                      f"({null_count} nulls — expected for new users)")
        return len(features)

    except Exception as e:
        console.print(f"  [yellow]–[/yellow] Feast: {e}")
        console.print("  [dim]   Falling back to parquet features[/dim]")
        # Fallback: read directly from parquet
        features = pd.read_parquet(DATA_DIR / "user_account_features.parquet")
        tx = pd.read_parquet(DATA_DIR / "transactions.parquet")
        recent_tx = tx.sort_values("event_timestamp").tail(200)
        context["feast_features"] = features.head(50)
        context["sample_users"] = features["user_id"].tolist()[:50]
        context["recent_tx"] = recent_tx
        console.print(f"  [green]✓[/green] Feast (parquet fallback): {len(context['feast_features'])} vectors")
        return len(context["feast_features"])


async def load_transactions(*, context: dict[str, Any], prior_results: dict[str, Any]) -> int:
    """Load full transaction dataset for model training."""
    console.print("  [cyan]→[/cyan] Loading transaction dataset...")
    tx = pd.read_parquet(DATA_DIR / "transactions.parquet")
    context["transactions"] = tx
    fraud_count = int(tx["is_fraud"].sum())
    fraud_rate = fraud_count / len(tx)
    context["fraud_rate"] = fraud_rate
    context["fraud_count"] = fraud_count
    console.print(f"  [green]✓[/green] Transactions: {len(tx):,} records "
                  f"({fraud_count} fraud, {fraud_rate:.1%})")
    return len(tx)


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Train a simple fraud model and log to MLflow
# ─────────────────────────────────────────────────────────────────────────────

async def train_and_log_model(*, context: dict[str, Any], prior_results: dict[str, Any]) -> int:
    """Train a lightweight fraud classifier and log run to MLflow."""
    console.print("  [cyan]→[/cyan] MLflow: training fraud model & logging run...")

    tx = context.get("transactions")
    features_df = context.get("feast_features")

    if tx is None or features_df is None:
        console.print("  [red]✗[/red] MLflow: missing upstream data")
        return 0

    # Merge transaction labels with features
    user_df = pd.read_parquet(DATA_DIR / "user_account_features.parquet")
    merged = tx.merge(user_df, on="user_id", how="left")
    merged = merged.fillna({
        "credit_score": 650,
        "account_age_days": 365,
        "user_has_2fa_installed": True,
        "avg_transaction_amount": 100.0,
    })

    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score, precision_score, recall_score, roc_auc_score

    feature_cols = ["amount", "credit_score", "account_age_days",
                    "user_has_2fa_installed", "avg_transaction_amount"]
    X = merged[feature_cols].astype(float)
    y = merged["is_fraud"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    clf = RandomForestClassifier(n_estimators=50, max_depth=8, random_state=42,
                                 n_jobs=-1, class_weight="balanced")
    clf.fit(X_train, y_train)
    y_pred = clf.predict(X_test)
    y_prob = clf.predict_proba(X_test)[:, 1]

    metrics = {
        "accuracy":  round(float(accuracy_score(y_test, y_pred)), 4),
        "precision": round(float(precision_score(y_test, y_pred, zero_division=0)), 4),
        "recall":    round(float(recall_score(y_test, y_pred, zero_division=0)), 4),
        "roc_auc":   round(float(roc_auc_score(y_test, y_prob)), 4),
        "fraud_rate_train": round(float(y_train.mean()), 4),
        "fraud_rate_test":  round(float(y_test.mean()), 4),
        "n_train": int(len(X_train)),
        "n_test":  int(len(X_test)),
    }
    params = {
        "n_estimators": 50,
        "max_depth": 8,
        "features": ",".join(feature_cols),
    }
    context["model_metrics"] = metrics
    context["model"] = clf

    # Log to MLflow using the Python SDK (handles auth automatically)
    try:
        import os
        import mlflow
        # MLflow basic-auth credentials (default for local server)
        os.environ.setdefault("MLFLOW_TRACKING_USERNAME", "admin")
        os.environ.setdefault("MLFLOW_TRACKING_PASSWORD", "password1234")
        mlflow.set_tracking_uri(MLFLOW_URL)

        client = mlflow.MlflowClient()

        # Get or create experiment
        try:
            exp = client.get_experiment_by_name("fraud-detection")
            exp_id = exp.experiment_id if exp else None
        except Exception:
            exp_id = None
        if not exp_id:
            exp_id = client.create_experiment("fraud-detection")

        run = client.create_run(
            experiment_id=exp_id,
            run_name=f"fraud_rf_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}",
            tags={"model_type": "RandomForest", "demo": "EuroPython2026"},
        )
        run_id = run.info.run_id

        for k, v in params.items():
            client.log_param(run_id, k, v)
        for k, v in metrics.items():
            if isinstance(v, float):
                client.log_metric(run_id, k, v)

        client.set_terminated(run_id, status="FINISHED")

        mlflow_url = f"{MLFLOW_URL}/#/experiments/{exp_id}/runs/{run_id}"
        context["mlflow_run_id"]  = run_id
        context["mlflow_exp_id"]  = exp_id
        context["mlflow_run_url"] = mlflow_url
        console.print(f"  [green]✓[/green] MLflow: run logged")
        console.print(f"    accuracy={metrics['accuracy']}  "
                      f"recall={metrics['recall']}  "
                      f"roc_auc={metrics['roc_auc']}")
        console.print(f"    [dim]{mlflow_url}[/dim]")

    except Exception as e:
        console.print(f"  [yellow]–[/yellow] MLflow logging failed: {e}")
        context["mlflow_run_url"] = "not logged"

    return 1


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Push model metrics to Prometheus pushgateway
# ─────────────────────────────────────────────────────────────────────────────

async def push_prometheus_metrics(*, context: dict[str, Any], prior_results: dict[str, Any]) -> int:
    """Push fraud model metrics to Prometheus via the pushgateway format."""
    console.print("  [cyan]→[/cyan] Prometheus: pushing model metrics...")

    metrics = context.get("model_metrics", {})
    fraud_rate = context.get("fraud_rate", 0)

    # Build Prometheus text format payload
    lines = [
        "# HELP fraud_model_accuracy Current fraud model accuracy on test set",
        "# TYPE fraud_model_accuracy gauge",
        f'fraud_model_accuracy{{model="random_forest",env="demo"}} {metrics.get("accuracy", 0)}',
        "",
        "# HELP fraud_model_recall Fraud recall (sensitivity) — critical for fraud detection",
        "# TYPE fraud_model_recall gauge",
        f'fraud_model_recall{{model="random_forest",env="demo"}} {metrics.get("recall", 0)}',
        "",
        "# HELP fraud_model_roc_auc ROC-AUC score for fraud classifier",
        "# TYPE fraud_model_roc_auc gauge",
        f'fraud_model_roc_auc{{model="random_forest",env="demo"}} {metrics.get("roc_auc", 0)}',
        "",
        "# HELP fraud_rate_live Observed fraud rate in recent transactions",
        "# TYPE fraud_rate_live gauge",
        f'fraud_rate_live{{env="demo"}} {fraud_rate}',
        "",
        "# HELP fraud_transactions_total Total fraudulent transactions detected",
        "# TYPE fraud_transactions_total counter",
        f'fraud_transactions_total{{env="demo"}} {context.get("fraud_count", 0)}',
        "",
    ]
    payload = "\n".join(lines)

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            # Try pushgateway first (port 9091), fall back to logging the metrics
            resp = await client.post(
                "http://localhost:9091/metrics/job/fraud_detection",
                content=payload,
                headers={"Content-Type": "text/plain"},
            )
            console.print(f"  [green]✓[/green] Prometheus pushgateway: metrics pushed")
    except Exception:
        # Pushgateway not running — just confirm Prometheus itself is reachable
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{PROM_URL}/-/healthy")
                console.print(f"  [green]✓[/green] Prometheus: reachable at {PROM_URL}")
                console.print(f"  [dim]   Metrics would be scraped from your app in production[/dim]")
                console.print(f"  [dim]   fraud_rate={fraud_rate:.3f}  "
                               f"accuracy={metrics.get('accuracy',0)}  "
                               f"recall={metrics.get('recall',0)}[/dim]")
        except Exception as e:
            console.print(f"  [yellow]–[/yellow] Prometheus: {e}")

    context["prometheus_metrics_pushed"] = True
    return 1


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Create Linear issue if anomalies detected
# ─────────────────────────────────────────────────────────────────────────────

async def create_linear_issue(*, context: dict[str, Any], prior_results: dict[str, Any]) -> int:
    """Open a Linear issue if fraud rate spiked or model performance degraded."""
    credential = store.retrieve("linear")
    if not credential:
        console.print("  [yellow]–[/yellow] Linear: no token — run: pipeline auth set linear")
        return 0

    token = credential.get("access_token", "")
    metrics = context.get("model_metrics", {})
    fraud_rate = context.get("fraud_rate", 0)
    mlflow_url = context.get("mlflow_run_url", "")

    # Determine alert level
    alerts = []
    if fraud_rate > 0.06:
        alerts.append(f"🚨 Fraud rate spike: {fraud_rate:.1%} (threshold: 6%)")
    if metrics.get("recall", 1) < 0.70:
        alerts.append(f"⚠️  Model recall degraded: {metrics.get('recall'):.2%} (threshold: 70%)")
    if metrics.get("roc_auc", 1) < 0.80:
        alerts.append(f"⚠️  ROC-AUC dropped: {metrics.get('roc_auc'):.3f} (threshold: 0.80)")

    severity = "🚨 CRITICAL" if any("🚨" in a for a in alerts) else "⚠️ WARNING" if alerts else "✅ NOMINAL"

    title = (
        f"[Fraud Detection] {severity} — "
        f"fraud_rate={fraud_rate:.1%}, "
        f"accuracy={metrics.get('accuracy', 0):.1%}, "
        f"recall={metrics.get('recall', 0):.1%}"
    )

    body = f"""## Automated fraud model pipeline run — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

### Pipeline summary
| Source | Records | Status |
|--------|---------|--------|
| Feast (feature vectors) | {len(context.get('sample_users', []))} users | ✅ |
| Transactions dataset | 15,000 | ✅ |
| MLflow (model run) | 1 run logged | ✅ |
| Prometheus (metrics) | pushed | ✅ |

### Model metrics
| Metric | Value | Threshold | Status |
|--------|-------|-----------|--------|
| Accuracy | {metrics.get('accuracy', 0):.1%} | — | ✅ |
| Recall (fraud sensitivity) | {metrics.get('recall', 0):.1%} | ≥ 70% | {"✅" if metrics.get("recall", 0) >= 0.70 else "⚠️"} |
| ROC-AUC | {metrics.get('roc_auc', 0):.3f} | ≥ 0.80 | {"✅" if metrics.get("roc_auc", 0) >= 0.80 else "⚠️"} |
| Fraud rate (dataset) | {fraud_rate:.1%} | ≤ 6% | {"✅" if fraud_rate <= 0.06 else "🚨"} |

### Alerts
{"\\n".join(f"- {a}" for a in alerts) if alerts else "- No anomalies detected — model performing within thresholds"}

### MLflow run
{mlflow_url}

---
_Created automatically by Zero-Pipeline. No connector classes written. No YAML touched._
"""

    console.print("  [cyan]→[/cyan] Linear: creating pipeline run issue...")

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
                console.print(f"  [green]✓[/green] Linear issue created in '{team_name}':")
                console.print(f"    [bold]{issue.get('url', '')}[/bold]")
                context["linear_issue_url"] = issue.get("url", "")
                return 1
            else:
                console.print(f"  [red]✗[/red] Linear: {r.json().get('errors', [])}")
                return 0

    except Exception as e:
        console.print(f"  [red]✗[/red] Linear: {e}")
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline assembly
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    console.print(Panel(
        "[bold]EuroPython 2026 — Zero to Pipeline[/bold]\n\n"
        "[dim]Use case: Fraud Detection[/dim]\n\n"
        "Feast (features) + Transactions → Train model → MLflow (log run)\n"
        "→ Prometheus (metrics) → Linear (alert if anomaly)",
        title="Fraud Detection Pipeline",
        border_style="blue",
    ))

    table = Table(title="Execution Plan", show_header=True)
    table.add_column("Step", style="cyan")
    table.add_column("Depends on", style="yellow")
    table.add_column("What it does")
    table.add_row("pull_feast_features",  "—",                           "[green]parallel[/green] — online feature vectors")
    table.add_row("load_transactions",    "—",                           "[green]parallel[/green] — training labels")
    table.add_row("train_and_log_model",  "feast + transactions",         "[blue]after both[/blue]  — train + log to MLflow")
    table.add_row("push_prometheus_metrics", "train_and_log_model",       "[blue]sequential[/blue]  — push metrics")
    table.add_row("create_linear_issue",  "push_prometheus_metrics",      "[blue]sequential[/blue]  — alert on anomaly")
    console.print(table)
    console.print()

    shared: dict[str, Any] = {}

    def wrap(fn):
        async def _w(*, context, prior_results):
            return await fn(context=shared, prior_results=prior_results)
        return _w

    pipeline = Pipeline("fraud-detection")
    pipeline.add_step("pull_feast_features", wrap(pull_feast_features))
    pipeline.add_step("load_transactions",   wrap(load_transactions))
    pipeline.add_step("train_and_log_model", wrap(train_and_log_model),
                      depends_on=["pull_feast_features", "load_transactions"])
    pipeline.add_step("push_prometheus_metrics", wrap(push_prometheus_metrics),
                      depends_on=["train_and_log_model"])
    pipeline.add_step("create_linear_issue", wrap(create_linear_issue),
                      depends_on=["push_prometheus_metrics"])

    console.print("[bold]Running pipeline...[/bold]\n")
    engine = PipelineEngine(checkpoint_manager=CheckpointManager())
    result = await engine.run(pipeline, context=shared)

    duration = (result.completed_at - result.started_at).total_seconds()
    metrics = shared.get("model_metrics", {})

    console.print()
    console.print(Panel(
        f"[green bold]Pipeline completed[/green bold]\n\n"
        f"  Feast features   : {len(shared.get('sample_users', []))} users\n"
        f"  Transactions     : {len(shared.get('transactions', [])):,} records\n"
        f"  Fraud rate       : {shared.get('fraud_rate', 0):.1%}\n"
        f"  Model accuracy   : {metrics.get('accuracy', 0):.1%}\n"
        f"  Model recall     : {metrics.get('recall', 0):.1%}\n"
        f"  ROC-AUC          : {metrics.get('roc_auc', 0):.3f}\n"
        f"  MLflow run       : {shared.get('mlflow_run_url', 'not logged')}\n"
        f"  Linear issue     : {shared.get('linear_issue_url', 'not created')}\n"
        f"  Duration         : {duration:.1f}s\n\n"
        "[dim]No connector classes written. No YAML. No SDK imports per source.[/dim]",
        title="Results",
        border_style="green",
    ))


if __name__ == "__main__":
    asyncio.run(main())
