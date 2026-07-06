# Zero to Pipeline: When Data Connectors Just Work

> **EuroPython 2025** — Data Engineering & MLOps Track (30 min)
>
> *Remember spending entire afternoons configuring data connectors, debugging OAuth flows, and writing custom parsers just to ingest data from a single API? What if you could just say "add Linear as a source" and have your pipeline automatically discover the API, handle authentication, and start ingesting data — no config files, no setup wizards?*

## Talk Outline

| Time | Section | What You'll See |
|------|---------|-----------------|
| 0:00–2:00 | **Opener + Promise** | The pain of connector setup; framing "Zero to Pipeline" |
| 2:00–6:00 | **What "Just Work" Means** | Success criteria: fast first sync, sane defaults, minimal input |
| 6:00–12:00 | **Architecture Overview** | The moving parts — registry, auth, extraction, orchestration |
| 12:00–17:00 | **Live Demo** | "add Linear as a source" → first data appears |
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
│  │   Known presets (linear, github, notion, slack, jira)               │  │
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
              │  └───────┘ └─────┘ └──────┘ │
              └───────────────────────────────┘
```

---

## How It Works

### 1. Source Discovery & Auto-Configuration

When you say `pipeline source add linear`, the framework:

1. **Checks the provider registry** — for known providers, returns pre-configured connection details instantly
2. **For unknown providers** — infers from the name (`api.{name}.com`), then optionally uses LLM to discover the real configuration
3. **Persists the source** — saves to `~/.zero-pipeline/workspaces/default/sources/{slug}/config.json`
4. **No config files needed** — works for ANY API name, known or unknown

```python
# MLOps / Data Engineering presets — instant config:
pipeline source add mlflow       # → localhost:5000, REST, offset pagination
pipeline source add wandb        # → api.wandb.ai, GraphQL cursor
pipeline source add airflow      # → localhost:8080, REST, basic auth
pipeline source add prometheus   # → localhost:9090, REST, no auth needed

# Unknown provider — LLM-discovers or infers from name:
pipeline source add my-feature-store   # → LLM discovers config
pipeline source add internal-api       # → api.internal-api.com (fallback)
```

### 2. LLM-Powered Intelligence (Pluggable Providers)

The framework uses LLM to supercharge discovery and self-healing. You choose which LLM backend to use:

| Provider | Config | Best For |
|----------|--------|----------|
| **Copilot** | `copilot` CLI installed | Legacy support, GitHub integration |
| **OpenAI** | `OPENAI_API_KEY` env var | GPT-4o, most capable |
| **Anthropic** | `ANTHROPIC_API_KEY` env var | Claude direct API |
| **Bedrock** | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` | Claude via AWS, enterprise/production |
| **Ollama** | Local server at `:11434` | Fully offline, privacy-first |

```bash
# Select explicitly:
export PIPELINE_LLM_PROVIDER=openai
export OPENAI_API_KEY=sk-...

# Or use Anthropic direct:
export PIPELINE_LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=sk-ant-...

# Or use Claude via AWS Bedrock:
export PIPELINE_LLM_PROVIDER=bedrock
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=us-east-1
# Optional: export AWS_SESSION_TOKEN=... (for assumed roles)
# Optional: export PIPELINE_LLM_MODEL=anthropic.claude-sonnet-4-20250514-v1:0

# Or let it auto-detect (tries copilot → openai → anthropic → bedrock → ollama)

# Override the model for any provider:
export PIPELINE_LLM_MODEL=gpt-4o-mini
```

The LLM powers four capabilities:
- **`plan_action`** — "list all open issues" → determines `GET /issues?state=open`
- **`heal_failure`** — API returns 400? LLM suggests corrected parameters
- **`discover_auth_docs`** — finds how to authenticate with any unknown API
- **`discover_provider_config`** — discovers base URL, pagination, endpoints for unknown APIs

**Everything works without LLM** — the registry and inference patterns handle the common cases. LLM is an accelerator for edge cases and unknown APIs.

### 3. Secure Authentication (Zero Token Pasting)

```
┌──────────┐         ┌──────────────┐         ┌─────────────┐
│   CLI    │────────▶│  Provider    │────────▶│   Browser   │
│          │ device  │  (Linear,    │ user     │  (user sees │
│          │ code    │   GitHub)    │ code     │  auth page) │
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

**API Key via Keyring** — for services with static tokens:
```bash
pipeline auth set linear   # prompted securely via getpass
# Token goes straight to OS keychain — never in .env, never in git
```

### 4. Self-Healing Connector

Every request goes through the `SelfHealingConnector` which automatically adapts:

| Failure | Healing Action |
|---------|---------------|
| `401` with `Bearer <token>` | Tries `<token>` without prefix |
| `401` with Authorization header | Tries `X-API-Key`, `Api-Key` headers |
| Rate limited (429) | Respects `Retry-After`, exponential backoff |
| Pagination breaks mid-stream | Resumes from last checkpoint cursor |
| Unknown pagination style | Infers from response (Link headers, cursor fields) |

```python
# The self-healing connector tries multiple auth formats automatically:
connector = SelfHealingConnector("https://api.linear.app", "lin_abc123")
response = await connector.request_with_healing("POST", "/graphql", json_body=query)
# If "Bearer lin_abc123" fails, tries "lin_abc123" (Linear's actual format) ✓
```

### 5. Pipeline Orchestration

```python
pipeline = Pipeline("my-pipeline")
pipeline.add_step("extract_linear", extract_linear_fn)
pipeline.add_step("extract_github", extract_github_fn)
pipeline.add_step("transform", transform_fn, depends_on=["extract_linear", "extract_github"])
pipeline.add_step("load", load_fn, depends_on=["transform"])

# Independent steps run in parallel, dependencies form a DAG
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
│   ├── data_pipeline/                  # Production package
│   │   ├── config.py                   # Settings (pydantic-settings + .env)
│   │   ├── cli.py                      # Typer CLI
│   │   ├── auth/
│   │   │   ├── credential_store.py     # OS keyring integration
│   │   │   ├── device_flow.py          # OAuth 2.0 Device Flow (RFC 8628)
│   │   │   └── manager.py             # Unified auth coordinator
│   │   ├── connectors/
│   │   │   ├── base.py                 # Universal APIConnector + pagination
│   │   │   ├── registry.py            # Provider presets + InferredConfig
│   │   │   ├── discovery.py           # API probing and schema inference
│   │   │   ├── self_healing.py        # Adaptive auth/pagination healing
│   │   │   └── llm_discovery.py       # LLM providers (OpenAI/Anthropic/Ollama/Copilot)
│   │   ├── sources/
│   │   │   └── store.py               # Persistent source state management
│   │   ├── orchestrator/
│   │   │   ├── checkpoint.py          # File-based cursor persistence
│   │   │   ├── pipeline.py           # DAG pipeline builder
│   │   │   └── engine.py             # Async execution engine
│   │   ├── extractors/
│   │   │   └── extract.py            # Orchestration-aware extraction
│   │   ├── loaders/
│   │   │   ├── base.py               # Loader interface
│   │   │   └── jsonl.py              # JSONL file loader
│   │   ├── observability/
│   │   │   ├── logging.py            # Structured logging (structlog)
│   │   │   └── metrics.py            # Counter/gauge/histogram
│   │   ├── mcp/
│   │   │   ├── server.py             # MCP tool server
│   │   │   └── stdio.py              # MCP stdio transport
│   │   └── schemas/
│   │       ├── source.py             # Source + InferredConfig models
│   │       ├── pipeline.py           # Pipeline execution models
│   │       └── records.py            # Data record models

├── tests/
│   ├── test_pipeline.py               # Orchestrator tests
│   ├── test_connectors.py            # Connector + registry + healing tests
│   └── test_auth.py                  # Auth module tests
├── examples/
│   ├── single_source_quick.py        # Minimal demo script
│   └── multi_source_pipeline.py      # Multi-source DAG example
└── pyproject.toml
```

---

## Quick Start

### Installation

```bash
git clone <repo-url>
cd dataingestionpydatahelenskidemo
uv sync --project python --extra dev
```

### First Pipeline (3 commands)

```bash
# 1. Add a source (auto-configured, persisted)
pipeline source add linear

# 2. Store credentials (in OS keychain, not .env)
pipeline auth set linear

# 3. Sync (uses default endpoint, with checkpointing)
pipeline sync run linear
```

### Multi-Source Pipeline

```bash
pipeline source add linear
pipeline source add github
pipeline source add notion

pipeline auth set linear
pipeline auth set github
pipeline auth set notion

# Each uses its default endpoint, all extract with checkpointing
pipeline sync run linear
pipeline sync run github
pipeline sync run notion
```

### Any Provider Works

```bash
# MLOps & Data Engineering presets — instant, zero config:
pipeline source add mlflow       # experiment tracking (local or remote)
pipeline source add wandb        # W&B runs, artifacts, sweeps
pipeline source add airflow      # DAG runs and task history
pipeline source add prometheus   # metrics and alerts
pipeline source add prefect      # flow runs and deployments
pipeline source add github       # CI/CD, repos, actions

# Unknown providers — LLM-discovered or name-inferred:
pipeline source add my-feature-store     # → LLM discovers endpoints
pipeline source add internal-monitoring  # → api.internal-monitoring.com
```

### Configure LLM Provider

```bash
# Use OpenAI:
export PIPELINE_LLM_PROVIDER=openai
export OPENAI_API_KEY=sk-...

# Or Anthropic (direct API):
export PIPELINE_LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=sk-ant-...

# Or Claude via AWS Bedrock (enterprise):
export PIPELINE_LLM_PROVIDER=bedrock
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=us-east-1

# Or local Ollama (fully offline):
export PIPELINE_LLM_PROVIDER=ollama
# (requires ollama running on localhost:11434)

# Or GitHub Copilot CLI (legacy):
export PIPELINE_LLM_PROVIDER=copilot
# (requires `copilot` CLI installed and authenticated)
```

### CLI Reference

```bash
# Source management
pipeline source add <provider>          # Add any source (persisted)
pipeline source add <provider> --base-url https://...  # Override URL
pipeline source list                    # Show YOUR added sources
pipeline source list-providers          # Show available presets
pipeline source discover <provider>     # Probe API capabilities
pipeline source test <provider>         # Test connectivity

# Authentication
pipeline auth set <provider>            # Store token in keychain
pipeline auth login <provider>          # OAuth Device Flow
pipeline auth status                    # Check auth state
pipeline auth revoke <provider>         # Remove credentials

# Data extraction
pipeline sync run <provider>            # Extract using default endpoint
pipeline sync run <provider> /custom/path  # Explicit endpoint
pipeline sync run <provider> --full     # Force full sync
pipeline sync run <provider> --no-heal  # Disable self-healing
pipeline sync status                    # Show checkpoints

# System
pipeline doctor                         # Health checks
pipeline mcp-server                     # Start MCP server
```

---

## Step-by-Step Walkthrough

This section walks through the complete workflow from zero to data flowing, exactly as you'd demo it on stage.

### Step 1: Install

```bash
git clone https://github.com/your-org/dataingestionpydatahelenskidemo.git
cd dataingestionpydatahelenskidemo
uv sync --project python --extra dev
```

Verify the installation (use `uv run --project python pipeline` or activate the venv first):
```bash
uv run --project python pipeline doctor
#   OK  Keyring accessible
#   OK  Provider registry loaded
#   OK  Checkpoint dir writable
#   OK  Source store accessible
# All checks passed!
```

### Step 2: Configure LLM (optional but recommended)

The LLM enables auto-discovery of unknown APIs. Choose one:

```bash
# Option A: AWS Bedrock (recommended for the demo)
export PIPELINE_LLM_PROVIDER=bedrock
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=us-east-1

# Option B: OpenAI
export PIPELINE_LLM_PROVIDER=openai
export OPENAI_API_KEY=sk-...

# Option C: Skip entirely (system still works, just uses name-based inference)
```

### Step 3: Add a Source

**Known provider (instant — uses demo preset):**
```bash
pipeline source add linear
# Source 'Linear' added (from: preset)
#   Base URL: https://api.linear.app
#   Auth: api_key
#   Pagination: graphql_cursor
#   Default endpoint: /graphql
```

**Unknown provider (auto-discovered via LLM or inferred from name):**
```bash
pipeline source add stripe
# Source 'Stripe' added (from: llm_discovered)
#   Base URL: https://api.stripe.com
#   Auth: bearer
#   Default endpoint: /v1/charges
```

**Completely custom/internal API:**
```bash
pipeline source add my-company-api --base-url https://api.internal.mycompany.com
# Source 'My-Company-Api' added (from: inferred)
#   Base URL: https://api.internal.mycompany.com
```

### Step 4: Store Credentials

Tokens are stored in your OS keychain (macOS Keychain / Linux Secret Service) — never in `.env`, never in git:

```bash
pipeline auth set linear
# Enter API token for linear: ****
# Token stored securely for linear

# Verify:
pipeline auth status
# ┏━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┓
# ┃ Provider ┃ Status            ┃
# ┡━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━┩
# │ linear   │ authenticated     │
# └──────────┴───────────────────┘
```

### Step 5: Test the Connection

```bash
pipeline source test linear
# Testing connection to https://api.linear.app...
# Connection to linear successful!
```

If the auth format is wrong, self-healing kicks in automatically during sync.

### Step 6: Run Your First Sync

```bash
pipeline sync run linear
# Syncing linear /graphql...
#   1. BUG-123: Fix login page timeout
#   2. FEAT-456: Add dark mode
#   3. TASK-789: Update dependencies
#   ...
# Done: 142 records extracted
# Output: ~/.zero-pipeline/output/linear/graphql.jsonl
```

What happened behind the scenes:
1. Loaded credentials from OS keychain
2. Detected GraphQL API → used POST with query body
3. Paginated through all results (cursor-based)
4. Saved checkpoint for incremental sync next time
5. Wrote records to JSONL file

### Step 7: Incremental Sync (only new data)

```bash
pipeline sync run linear
# Resuming from checkpoint cursor: eyJjdXJzb3IiOi...
# Done: 3 records extracted (only changes since last sync)
```

Force a full re-sync:
```bash
pipeline sync run linear --full
# Checkpoint cleared — full sync
# Done: 142 records extracted
```

### Step 8: Natural Language Interface

Instead of remembering commands, just say what you want:

```bash
pipeline chat "add notion as a source"
# Adding source: notion
# Source 'Notion' added (from: preset)

pipeline chat "sync data from linear"
# Syncing from linear...
# Done: 142 records extracted

pipeline chat "show me my sources"
# [lists sources table]
```

### Step 9: Multi-Source Pipeline

```bash
# Add multiple sources
pipeline source add linear
pipeline source add github
pipeline source add notion

# Auth each
pipeline auth set linear
pipeline auth set github
pipeline auth set notion

# Sync all (each uses its default endpoint)
pipeline sync run linear
pipeline sync run github
pipeline sync run notion

# All data lands in ~/.zero-pipeline/output/{provider}/
ls ~/.zero-pipeline/output/
# linear/  github/  notion/
```

### Step 10: Inspect Your Data

```bash
# View extracted records
cat ~/.zero-pipeline/output/linear/graphql.jsonl | head -3
# {"id": "issue_1", "source_id": "linear", "resource_type": "graphql", "raw_data": {...}}
# {"id": "issue_2", "source_id": "linear", "resource_type": "graphql", "raw_data": {...}}

# Check sync status and checkpoints
pipeline sync status
# ┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┓
# ┃ Source           ┃ Cursor          ┃ Last Sync           ┃
# ┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━┩
# │ linear:/graphql  │ eyJjdX...       │ 2025-07-05 14:30:00 │
# └──────────────────┴─────────────────┴─────────────────────┘
```

### The "Zero to Pipeline" Promise in Action

```bash
# From absolutely nothing to data flowing — three commands:
pipeline source add linear          # 1. Auto-discover the API
pipeline auth set linear            # 2. Store credentials securely
pipeline sync run linear            # 3. Data flows

# No YAML. No config files. No setup wizards. No custom parsers.
# Just name the API and go.
```

---

## The Reliability Playbook

### Why Not Temporal?

| Concern | Temporal | This Framework |
|---------|----------|---------------|
| Infrastructure | Requires cluster + workers | Zero — runs in-process |
| Debugging | gRPC + Rust core = opaque | Pure Python asyncio = transparent |
| Learning curve | SDK + concepts + deployment | Standard library patterns |
| Demo-ability | 10 min explaining infra | 30 seconds explaining the code |

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
# Cursors are persisted after each batch
state = CheckpointState(pipeline_id="run_abc", source_id="linear", cursor="abc123")
checkpoint_manager.save(state)

# On restart, extraction resumes from last cursor — no replay
checkpoint = checkpoint_manager.load("run_abc", "linear")
# "Resuming from checkpoint cursor: abc123"
```

### Self-Healing Auth

```python
# First attempt: Bearer tok123 → 401
# Healing: tries tok123 (no prefix) → 200 ✓
# Learned: Linear doesn't want Bearer prefix
# Next request uses the corrected format automatically
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
{"event": "extraction_complete", "source": "linear", "resource": "issues", "records": 142}
{"event": "self_healing_auth", "base_url": "https://api.linear.app", "error": "401"}
{"event": "healing_success", "new_prefix": "", "new_header": "Authorization"}
{"event": "pipeline_completed", "total_records": 847, "duration_ms": 12340}
```

### Alert-Worthy Signals

| Signal | Meaning |
|--------|---------|
| `step_failed` after max retries | Source down or creds expired |
| `rate_limited` | Approaching API limits |
| `healing_success` | Auto-corrected a misconfiguration |
| `llm_no_provider_available` | No LLM for discovery — using fallback |

---

## Connecting to ANY API (No Custom Code)

The whole point: you **don't** write per-provider connector classes.

```python
from data_pipeline.connectors import SelfHealingConnector, OffsetPagination

# Connect to ANY API — no provider-specific code:
connector = SelfHealingConnector(
    "https://api.your-service.com",
    credential="your-api-key",
    auth_prefix="Bearer",
)

# Extract with self-healing:
async for record in connector.extract_with_healing(
    "GET", "/v1/items",
    pagination=OffsetPagination(page_size=100),
    source_id="your-service",
    resource_type="items",
):
    process(record)
```

**To accelerate a known API** (optional preset):

```python
from data_pipeline.connectors.registry import ProviderPreset, provider_registry
from data_pipeline.schemas import AuthType

provider_registry.register("your-api", ProviderPreset(
    name="Your API",
    base_url="https://api.your-service.com",
    auth_type=AuthType.API_KEY,
    pagination_style="cursor",
    default_endpoints={"items": "/v1/items", "users": "/v1/users"},
))
```

---

## Development

```bash
# Install with dev dependencies
uv sync --project python --extra dev

# Run tests
uv run --project python --directory python pytest tests/ -v

# Lint
uv run --project python ruff check python/src/data_pipeline/

# Type check
uv run --project python mypy python/src/data_pipeline/
```

---

## Previous Presentations

- [PyData Helsinki — Google Slides](https://docs.google.com/presentation/d/1iWNqe-PDDbrXpOal0TMOAvmb7SS7AzQsf6DyEvkgFMI/edit?usp=sharing)

### Photos

[![PyData Helsinki Photo 1](https://www.meetup.com/pydatahelsinki/photos/35854184/532932617/)](https://www.meetup.com/pydatahelsinki/photos/35854184/532932617/)
[![PyData Helsinki Photo 2](https://www.meetup.com/pydatahelsinki/photos/35854184/532932618/)](https://www.meetup.com/pydatahelsinki/photos/35854184/532932618/)

---

## License

MIT
