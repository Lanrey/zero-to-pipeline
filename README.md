# Zero to Pipeline: When Data Connectors Just Work

> **EuroPython 2026** — Data Engineering & MLOps Track (30 min)
>
> *Remember spending entire afternoons configuring data connectors, debugging OAuth flows, and writing custom parsers just to ingest data from a single API? What if you could just say "add MLflow as a source" and have your pipeline automatically discover the API, handle authentication, and start ingesting data — no config files, no setup wizards?*

## Talk Outline

| Time | Section | What You'll See |
|------|---------|-----------------|
| 0:00–2:00 | **Opener + Promise** | The pain of connector setup; framing "Zero to Pipeline" |
| 2:00–6:00 | **What "Just Work" Means** | Success criteria: fast first sync, sane defaults, minimal input |
| 6:00–12:00 | **Architecture Overview** | The moving parts — registry, auth, extraction, orchestration |
| 12:00–17:00 | **Live Demo** | "add MLflow as a source" → experiment runs flowing |
| 17:00–23:00 | **Reliability Playbook** | Retries, checkpointing, idempotency, rate limits |
| 23:00–25:00 | **Takeaways** | Checklist for building self-configuring connectors |
| 25:00–30:00 | **Q&A** | |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      Zero to Pipeline Framework                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐ │
│  │   CLI    │    │  MCP Server  │    │  AI Agent    │    │  Python API  │ │
│  │ (typer)  │    │   (stdio)    │    │ Integration  │    │  (import)    │ │
│  └────┬─────┘    └──────┬───────┘    └──────┬───────┘    └──────┬───────┘ │
│       └──────────────────┼────────────────────┼──────────────────┘         │
│                          ▼                    ▼                             │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                    Provider Registry                                 │  │
│  │   Known presets (mlflow, wandb, airflow, prometheus, prefect)       │  │
│  │   + Dynamic inference for ANY unknown provider                      │  │
│  └────────────────────────────────┬────────────────────────────────────┘  │
│                                   │                                        │
│                                   ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                    LLM Discovery Engine                              │  │
│  │   ┌──────────┐ ┌──────────┐ ┌───────────┐ ┌──────────┐            │  │
│  │   │ Copilot  │ │  OpenAI  │ │ Anthropic │ │  Ollama  │            │  │
│  │   └──────────┘ └──────────┘ └───────────┘ └──────────┘            │  │
│  │   Plan actions · Heal failures · Discover auth · Infer config      │  │
│  └────────────────────────────────┬────────────────────────────────────┘  │
│                                   │                                        │
│       ┌───────────────────────────┼───────────────────────┐               │
│       ▼                           ▼                       ▼               │
│  ┌───────────────┐     ┌────────────────────┐    ┌──────────────────┐   │
│  │  Self-Healing │     │  Universal API     │    │  Pipeline        │   │
│  │   Connector   │     │  Connector         │    │  Orchestrator    │   │
│  │               │     │  (any REST/GraphQL)│    │  (async DAG)     │   │
│  │ Auth healing  │     │  Retry + backoff   │    │  Checkpointing   │   │
│  │ Format adapt  │     │  Rate limiting     │    │  Parallel steps  │   │
│  │ Pagination    │     │  Pagination        │    │  Retry per step  │   │
│  │   inference   │     │  Schema inference  │    │                  │   │
│  └───────┬───────┘     └────────┬───────────┘    └────────┬─────────┘   │
│          │                      │                          │              │
│          └──────────────────────┼──────────────────────────┘              │
│                                 ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                    Secure Auth Layer                                  │  │
│  │  ┌───────────────┐  ┌────────────────────┐  ┌────────────────────┐ │  │
│  │  │ OS Keyring    │  │ OAuth Device Flow  │  │ Credential Store   │ │  │
│  │  │ (macOS/Linux) │  │ (RFC 8628)         │  │ (per-source)       │ │  │
│  │  └───────────────┘  └────────────────────┘  └────────────────────┘ │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                            │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                    Source Store (persistent)                          │  │
│  │  ~/.zero-pipeline/workspaces/default/sources/{slug}/config.json     │  │
│  │  Tracks: connection status, last sync, default endpoint, metadata   │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
              ┌───────────────────────────────┐
              │        Destinations           │
              │  ┌───────┐ ┌─────┐ ┌──────┐ │
              │  │ JSONL  │ │ S3  │ │  DB  │ │
              └──┴───────┴─┴─────┴─┴──────┘─┘
```

---

## How It Works

### 1. Source Discovery & Auto-Configuration

When you say `pipeline source add mlflow`, the framework:

1. **Checks the provider registry** — for known providers, returns pre-configured connection details instantly
2. **LLM enriches all providers** — even presets get enriched with deeper endpoint knowledge
3. **For unknown providers** — LLM discovers the full config, falls back to name-based inference
4. **Persists the source** — saves to `~/.zero-pipeline/workspaces/default/sources/{slug}/config.json`

```bash
# MLOps & Data Engineering presets — instant config:
pipeline source add mlflow       # experiment tracking (local or remote)
pipeline source add wandb        # W&B runs, artifacts, sweeps
pipeline source add airflow      # DAG runs and task history
pipeline source add prometheus   # metrics and alerts
pipeline source add prefect      # flow runs and deployments
pipeline source add github       # CI/CD runs, repos, actions

# Unknown providers — LLM-discovers or infers from name:
pipeline source add my-feature-store     # → LLM discovers endpoints
pipeline source add internal-monitoring  # → api.internal-monitoring.com (fallback)
```

### 2. LLM-Powered Intelligence (Pluggable Providers)

The framework uses LLM to supercharge discovery and self-healing for **all providers** — presets included. Choose your backend:

| Provider | Config | Best For |
|----------|--------|----------|
| **Bedrock** | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` | Claude via AWS, enterprise/production |
| **Anthropic** | `ANTHROPIC_API_KEY` env var | Claude direct API |
| **OpenAI** | `OPENAI_API_KEY` env var | GPT-4o |
| **Ollama** | Local server at `:11434` | Fully offline, air-gapped ML infra |
| **Copilot** | `copilot` CLI installed | GitHub Copilot CLI (legacy) |

```bash
# AWS Bedrock (recommended for production):
export PIPELINE_LLM_PROVIDER=bedrock
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=us-east-1

# Or let it auto-detect (tries copilot → openai → anthropic → bedrock → ollama)

# Override the model for any provider:
export PIPELINE_LLM_MODEL=anthropic.claude-sonnet-4-20250514-v1:0
```

LLM powers four capabilities:
- **`plan_action`** — "fetch all failed runs from last week" → determines the right API call
- **`heal_failure`** — API returns 400? LLM suggests corrected parameters
- **`discover_auth_docs`** — finds how to authenticate with any unknown API
- **`discover_provider_config`** — discovers base URL, pagination, endpoints for unknown APIs

**Everything works without LLM** — presets and name-based inference cover the common cases. LLM is an accelerator for edge cases and internal APIs.

### 3. Secure Authentication (Zero Token Pasting)

```
┌──────────┐         ┌──────────────┐         ┌─────────────┐
│   CLI    │────────▶│  Provider    │────────▶│   Browser   │
│          │ device  │  (MLflow,    │ user     │  (user sees │
│          │ code    │   W&B, etc.) │ code     │  auth page) │
│          │◀────────│              │◀─────────│             │
│          │ tokens  │              │ approved │             │
└──────────┘         └──────────────┘         └─────────────┘
     │
     ▼
┌──────────┐
│ OS       │  Tokens stored in macOS Keychain / Linux Secret Service
│ Keyring  │  Never touch disk as plaintext
└──────────┘
```

```bash
pipeline auth set mlflow   # prompted securely via getpass
# Token goes straight to OS keychain — never in .env, never in git
```

### 4. Self-Healing Connector

Every request goes through the `SelfHealingConnector` which automatically adapts:

| Failure | Healing Action |
|---------|----------------|
| `401` with `Bearer <token>` | Tries `<token>` without prefix |
| `401` with Authorization header | Tries `X-API-Key`, `Api-Key` headers |
| Rate limited (429) | Respects `Retry-After`, exponential backoff |
| Pagination breaks mid-stream | Resumes from last checkpoint cursor |
| Unknown pagination style | Infers from response (Link headers, cursor fields) |

```python
# MLflow self-hosted may reject "Bearer" prefix depending on version.
# The healing connector figures it out automatically:
connector = SelfHealingConnector("http://mlflow-server:5000", "mlf_tok_abc123")
# Try 1: "Authorization: Bearer mlf_tok_abc123" → 401
# Try 2: "Authorization: mlf_tok_abc123"        → 200 ✓
# Saved. All subsequent requests use the corrected format.
```

### 5. Pipeline Orchestration

```python
pipeline = Pipeline("mlops-data-ingest")
pipeline.add_step("extract_mlflow",     extract_mlflow_fn)
pipeline.add_step("extract_wandb",      extract_wandb_fn)
pipeline.add_step("extract_prometheus", extract_prometheus_fn)
pipeline.add_step("transform", transform_fn,
    depends_on=["extract_mlflow", "extract_wandb", "extract_prometheus"])

# Steps 1-3 run in parallel, step 4 runs when all three complete
result = await engine.run(pipeline)
```

### 6. MCP Server Integration

The framework exposes pipeline operations as MCP tools for AI assistants:

```json
{"tools": [
  {"name": "add_source", "description": "Add any data source"},
  {"name": "sync_source", "description": "Trigger extraction"},
  {"name": "list_sources", "description": "List configured sources"},
  {"name": "test_connection", "description": "Verify connectivity"}
]}
```

---

## Project Structure

```
python/
├── src/
│   └── data_pipeline/
│       ├── config.py                   # Settings (pydantic-settings + .env)
│       ├── cli.py                      # Typer CLI
│       ├── auth/
│       │   ├── credential_store.py     # OS keyring integration
│       │   ├── device_flow.py          # OAuth 2.0 Device Flow (RFC 8628)
│       │   └── manager.py             # Unified auth coordinator
│       ├── connectors/
│       │   ├── base.py                 # Universal APIConnector + pagination
│       │   ├── registry.py            # Provider presets + InferredConfig
│       │   ├── discovery.py           # API probing and schema inference
│       │   ├── self_healing.py        # Adaptive auth/pagination healing
│       │   └── llm_discovery.py       # LLM backends (Bedrock/OpenAI/Anthropic/Ollama)
│       ├── sources/
│       │   └── store.py               # Persistent source state management
│       ├── orchestrator/
│       │   ├── checkpoint.py          # File-based cursor persistence
│       │   ├── pipeline.py            # DAG pipeline builder
│       │   └── engine.py             # Async execution engine
│       ├── extractors/
│       │   └── extract.py            # Orchestration-aware extraction
│       ├── loaders/
│       │   ├── base.py               # Loader interface
│       │   └── jsonl.py              # JSONL file loader
│       ├── observability/
│       │   ├── logging.py            # Structured logging (structlog)
│       │   └── metrics.py            # Counter/gauge/histogram
│       ├── mcp/
│       │   ├── server.py             # MCP tool server
│       │   └── stdio.py              # MCP stdio transport
│       └── schemas/
│           ├── source.py             # Source + InferredConfig models
│           ├── pipeline.py           # Pipeline execution models
│           └── records.py            # Data record models
├── tests/
│   ├── test_pipeline.py               # Orchestrator tests
│   ├── test_connectors.py            # Connector + registry + healing tests
│   └── test_auth.py                  # Auth module tests
├── examples/
│   ├── single_source_quick.py        # Single-source demo (MLflow)
│   └── multi_source_pipeline.py      # MLOps multi-source DAG demo
└── pyproject.toml
```

---

## Quick Start

### Installation

```bash
git clone https://github.com/Lanrey/zero-to-pipeline.git
cd zero-to-pipeline
uv sync --project python --extra dev
```

### First Pipeline (3 commands)

```bash
# 1. Add a source (auto-configured, persisted)
pipeline source add mlflow

# 2. Store credentials (in OS keychain, not .env)
pipeline auth set mlflow

# 3. Sync (uses default endpoint, with checkpointing)
pipeline sync run mlflow
```

### Multi-Source MLOps Pipeline

```bash
pipeline source add mlflow
pipeline source add wandb
pipeline source add prometheus

pipeline auth set mlflow
pipeline auth set wandb

# Each extracts in parallel with checkpointing
pipeline sync run mlflow
pipeline sync run wandb
pipeline sync run prometheus
```

### CLI Reference

```bash
# Source management
pipeline source add <provider>                      # Add any source (persisted)
pipeline source add <provider> --base-url https://  # Override URL (e.g. self-hosted MLflow)
pipeline source list                                # Show your added sources
pipeline source list-providers                      # Show available presets
pipeline source discover <provider>                 # Probe API capabilities
pipeline source test <provider>                     # Test connectivity
pipeline source remove <provider>                   # Remove a source

# Authentication
pipeline auth set <provider>       # Store token in OS keychain
pipeline auth login <provider>     # OAuth Device Flow
pipeline auth status               # Check auth state for all sources
pipeline auth revoke <provider>    # Remove stored credentials

# Data extraction
pipeline sync run <provider>                # Extract using default endpoint
pipeline sync run <provider> /custom/path  # Explicit endpoint
pipeline sync run <provider> --full        # Force full sync (clears checkpoint)
pipeline sync run <provider> --no-heal     # Disable self-healing
pipeline sync status                       # Show active checkpoints

# Natural language
pipeline chat "add wandb as a source"
pipeline chat "sync my mlflow runs"
pipeline chat "show me my sources"

# System
pipeline doctor       # Health checks
pipeline mcp-server   # Start MCP server for AI assistant integration
```

---

## Step-by-Step Walkthrough

### Step 1: Install

```bash
git clone https://github.com/Lanrey/zero-to-pipeline.git
cd zero-to-pipeline
uv sync --project python --extra dev
```

```bash
uv run --project python pipeline doctor
#   OK  Keyring accessible
#   OK  Provider registry loaded
#   OK  Checkpoint dir writable
#   OK  Source store accessible
# All checks passed!
```

### Step 2: Configure LLM (optional)

```bash
# AWS Bedrock (recommended for enterprise):
export PIPELINE_LLM_PROVIDER=bedrock
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=us-east-1

# Or skip entirely — system works on name-based inference alone
```

### Step 3: Add a Source

```bash
# Known MLOps provider — instant from preset:
pipeline source add mlflow
# Source 'MLflow' added (from: preset+llm)
#   Base URL: http://localhost:5000  (override with --base-url for remote)
#   Auth: api_key
#   Default endpoint: /api/2.0/mlflow/runs/search

# Self-hosted on a remote server:
pipeline source add mlflow --base-url http://mlflow.internal:5000

# Internal/unknown API — LLM discovers the config:
pipeline source add my-feature-store --base-url https://features.internal.co
# Source 'My-Feature-Store' added (from: llm_discovered)
#   Base URL: https://features.internal.co
#   Endpoints discovered: /v1/features, /v1/entities
```

### Step 4: Store Credentials

```bash
pipeline auth set mlflow
# Enter API token for mlflow: ****
# Token stored securely for mlflow

pipeline auth status
# ┏━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┓
# ┃ Provider   ┃ Status            ┃
# ┡━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━┩
# │ mlflow     │ authenticated     │
# └────────────┴───────────────────┘
```

### Step 5: Test the Connection

```bash
pipeline source test mlflow
# Testing connection to http://localhost:5000...
# Connection to mlflow successful!
```

### Step 6: Run Your First Sync

```bash
pipeline sync run mlflow
# Syncing mlflow /api/2.0/mlflow/runs/search...
#   1. run_abc123: experiment=training-v2, status=FINISHED
#   2. run_def456: experiment=training-v2, status=RUNNING
#   3. run_ghi789: experiment=baseline, status=FINISHED
#   ...
# Done: 8,421 records extracted
# Output: ~/.zero-pipeline/output/mlflow/runs.jsonl
```

### Step 7: Incremental Sync

```bash
pipeline sync run mlflow
# Resuming from checkpoint cursor: 8421
# Done: 12 records extracted   (only new runs since last sync)

pipeline sync run mlflow --full
# Checkpoint cleared — full sync
# Done: 8,421 records extracted
```

### Step 8: Natural Language Interface

```bash
pipeline chat "add wandb as a source"
# Source 'Weights & Biases' added.

pipeline chat "sync my mlflow experiment runs"
# Done: 12 new runs extracted.

pipeline chat "show me all my data sources"
```

### Step 9: Multi-Source Pipeline

```bash
pipeline source add mlflow
pipeline source add wandb
pipeline source add prometheus

pipeline auth set mlflow
pipeline auth set wandb

pipeline sync run mlflow
pipeline sync run wandb
pipeline sync run prometheus

ls ~/.zero-pipeline/output/
# mlflow/  wandb/  prometheus/
```

### Step 10: Inspect Your Data

```bash
cat ~/.zero-pipeline/output/mlflow/runs.jsonl | head -1 | python -m json.tool
# {
#   "id": "run_abc123",
#   "source_id": "mlflow",
#   "resource_type": "runs",
#   "raw_data": { "run_id": "abc123", "status": "FINISHED", ... }
# }

pipeline sync status
# ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┓
# ┃ Source                             ┃ Cursor ┃ Last Sync           ┃
# ┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━┩
# │ mlflow:/api/2.0/mlflow/runs/search │ 8421   │ 2026-07-06 14:30:00 │
# └────────────────────────────────────┴────────┴─────────────────────┘
```

### The Promise in Action

```bash
# From nothing to data flowing — three commands:
pipeline source add mlflow     # 1. Auto-discover the API
pipeline auth set mlflow       # 2. Store credentials securely
pipeline sync run mlflow       # 3. Data flows

# No YAML. No config files. No setup wizards. No custom parsers.
```

---

## The Reliability Playbook

### Why Not Temporal?

| Concern | Temporal | This Framework |
|---------|----------|----------------|
| Infrastructure | Requires cluster + workers | Zero — runs in-process |
| Debugging | gRPC + Rust core = opaque | Pure Python asyncio = transparent |
| Learning curve | SDK + concepts + deployment | Standard library patterns |

**When to reach for Temporal**: Distributed execution across machines, very long-running workflows, complex compensation/saga patterns.

### Retries with Exponential Backoff

```python
@retry(
    retry=retry_if_exception_type((httpx.TransportError, RateLimitError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, max=60),
)
async def request(self, method, path, **kwargs):
    ...
```

### Checkpointing (Cursor/Watermark)

```python
# Cursor persisted after each batch of 100 records
state = CheckpointState(
    pipeline_id="mlops-ingest",
    source_id="mlflow",
    cursor="8421",
)
checkpoint_manager.save(state)

# Crash at record 5,000? Restart resumes at 5,001.
checkpoint = checkpoint_manager.load("mlops-ingest", "mlflow")
```

### Self-Healing Auth

```python
# MLflow self-hosted may reject "Bearer" prefix on some versions.
# Try 1: "Authorization: Bearer mlf_tok" → 401
# Try 2: "Authorization: mlf_tok"        → 200 ✓
# Saved — no manual config change needed.
```

### Rate Limit Handling

```python
if response.status_code == 429:
    retry_after = float(response.headers.get("Retry-After", "60"))
    await asyncio.sleep(retry_after)
    raise RateLimitError(retry_after)  # triggers tenacity retry
```

---

## Observability

### Structured Logging (JSON)

```json
{"event": "pipeline_started",   "pipeline": "mlops-data-ingest",  "run_id": "sync_a1b2c3"}
{"event": "step_started",       "step": "extract_mlflow",         "source": "mlflow"}
{"event": "healing_success",    "new_prefix": "",                  "new_header": "Authorization"}
{"event": "step_completed",     "step": "extract_mlflow",         "records": 8421, "duration_ms": 3200}
{"event": "pipeline_completed", "total_records": 9103,            "duration_ms": 4850}
```

### Alert-Worthy Signals

| Signal | Meaning |
|--------|---------|
| `step_failed` after max retries | Source down or credentials expired |
| `rate_limited` | Approaching API limits |
| `healing_success` | Auth format corrected — upstream API may have changed |
| `llm_no_provider_available` | No LLM configured — using fallback inference |

---

## Connecting to ANY API

You don't write per-provider connector classes. The universal connector handles any REST or GraphQL API:

```python
from data_pipeline.connectors import SelfHealingConnector, OffsetPagination

connector = SelfHealingConnector(
    "http://mlflow-server:5000",
    credential="mlf_tok_abc123",
    auth_prefix="Bearer",
)

async for record in connector.extract_with_healing(
    "GET", "/api/2.0/mlflow/runs/search",
    pagination=OffsetPagination(page_size=100),
    source_id="mlflow",
    resource_type="runs",
):
    process(record)
```

**To accelerate a known API** (optional preset):

```python
from data_pipeline.connectors.registry import ProviderPreset, provider_registry
from data_pipeline.schemas import AuthType

provider_registry.register("my-feature-store", ProviderPreset(
    name="Feature Store",
    base_url="https://features.internal.co",
    auth_type=AuthType.API_KEY,
    pagination_style="cursor",
    default_endpoints={
        "features": "/v1/features",
        "entities": "/v1/entities",
    },
))
```

---

## Development

```bash
# Install with dev dependencies
uv sync --project python --extra dev

# Run tests (40 tests)
python/.venv/bin/pytest python/tests/ -v

# Lint
uv run --project python ruff check python/src/data_pipeline/

# Type check
uv run --project python mypy python/src/data_pipeline/
```

---

## Presentation

**EuroPython 2026** — [presentation/index.html](presentation/index.html)

Open in any browser. Press `S` for speaker view (notes + timer).

---

## License

MIT
