# Zero to Pipeline

**Self-configuring data ingestion — connect any API without writing connectors.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)]()
[![License: MIT](https://img.shields.io/badge/license-MIT-green)]()

Zero to Pipeline is a Python framework that turns any REST or GraphQL API into a data source in a single command. No connector classes, no YAML schemas, no pagination boilerplate. The framework uses an LLM (your own API key) to discover auth type, endpoints, and pagination style from a provider name — then extracts data with self-healing, checkpointed syncs.

---

## Why Zero to Pipeline?

Traditional ETL tools require you to write a connector for every API. Each one hard-codes the base URL, auth format, pagination strategy, and response schema. This doesn't scale.

Zero to Pipeline inverts the problem: **you say what to connect, and the framework figures out how.**

| Problem | Solution |
|---------|----------|
| Each API needs a custom connector | LLM discovers config from the provider name |
| Auth breaks between API versions | Self-healing rotates header formats on 401/403 |
| Pagination is subtly different per API | Auto-detects cursor, offset, link-header, and GraphQL |
| Full re-syncs are wasteful | Checkpointed extraction — resumes from the last cursor |
| Credentials leak into config files | OS keychain storage, never plaintext on disk |

---

## Quick start

```bash
# Install (Python 3.11+)
pip install zero-pipeline

# Store your LLM API key (for auto-discovery)
pipeline auth set openai --token sk-proj-...

# Add a data source — any API name works
pipeline source add mlflow

# Test the connection
pipeline source test mlflow

# Extract data (first run: full sync; subsequent runs: incremental)
pipeline sync run mlflow
```

---

## Use cases

**Data engineering teams** — ingest from MLflow, W&B, Airflow, Prometheus, Grafana, or any internal API without writing and maintaining connector code.

**ML platform teams** — pull experiment metadata, run metrics, and model registry data from experiment tracking tools into your data lake.

**Platform engineers** — connect to GitHub, Linear, Notion, or Prefect and sync issues, projects, and workflow state into a queryable format.

**Anyone building internal tools** — point at any REST or GraphQL API and get checkpointed, paginated extraction in minutes instead of days.

---

## How it works

```
pipeline source add <provider>
        │
        ▼
┌─────────────────────┐
│  1. Provider         │  Known presets (MLflow, GitHub, Airflow, etc.)
│     Registry         │  give instant base URL, auth, pagination
└────────┬────────────┘
         │ enriches
         ▼
┌─────────────────────┐
│  2. LLM Discovery    │  Your LLM fills gaps: endpoints, rate limits,
│                      │  API quirks. Unknown providers get full config.
└────────┬────────────┘
         │ validates
         ▼
┌─────────────────────┐
│  3. HTTP Probing     │  Checks reachability, detects pagination
│                      │  from response headers and body shape
└────────┬────────────┘
         │ connects
         ▼
┌─────────────────────┐
│  4. Self-Healing     │  Rotates auth formats on 401/403.
│     Connector        │  Resumes from last cursor after healing.
└────────┬────────────┘
         │ extracts
         ▼
┌─────────────────────┐
│  5. Checkpointed     │  Saves cursor every 100 records.
│     Extraction       │  Next run: resumes, doesn't replay.
└─────────────────────┘
```

---

## Supported providers

Known presets (demo accelerators — any API name works without them):

| Provider | Category | Auth | Pagination |
|----------|----------|------|------------|
| MLflow | ML Experiment Tracking | None | Offset |
| Weights & Biases | ML Experiment Tracking | API Key | Cursor |
| Feast | Feature Store | None | Offset |
| Prometheus | Observability / Monitoring | None | Offset |
| Grafana | Observability / Dashboards | API Key | Offset |
| Apache Airflow | Workflow Orchestration | Basic | Offset |
| Prefect | Dataflow Automation | API Key | Offset |
| GitHub | Version Control / CI/CD | OAuth2 | Link Header |
| Linear | Issue Tracking | API Key | GraphQL Cursor |
| Notion | Docs / Knowledge Base | OAuth2 | Cursor |

Unknown providers fall through to LLM-driven discovery. Use `--base-url` for internal APIs:

```bash
pipeline source add my-feature-store --base-url https://features.internal.co
```

---

## LLM provider (BYOK — bring your own key)

| Provider | Recommended model | API key |
|----------|------------------|---------|
| OpenAI (default) | `gpt-4o` | `OPENAI_API_KEY` or `pipeline auth set openai` |
| Anthropic | `claude-sonnet-4-20250514` | `ANTHROPIC_API_KEY` or `pipeline auth set anthropic` |
| Ollama (local) | `llama3.2` | None needed |
| Azure OpenAI | per deployment | `pipeline auth set openai` |
| Groq, Together, vLLM, LM Studio | per model | Per service |

The OpenAI-compatible provider works with any `/v1/chat/completions` endpoint. Local inference servers (Ollama, vLLM) need no API key.

---

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `PIPELINE_LLM_PROVIDER` | `openai` | LLM provider: `openai` or `anthropic` |
| `PIPELINE_LLM_MODEL` | per-provider | Model ID |
| `PIPELINE_LLM_BASE_URL` | per-provider | Override for Azure, Ollama, Groq |
| `PIPELINE_LOG_LEVEL` | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `PIPELINE_DEFAULT_TIMEOUT` | `30` | HTTP request timeout in seconds |
| `PIPELINE_MAX_RETRIES` | `3` | Maximum retry attempts for failed requests |

All settings support `.env` files. See [docs/reference.md](docs/reference.md) for the complete list.

---

## Project structure

```
src/data_pipeline/
├── cli/                 # CLI commands (package, not a single file)
│   ├── __init__.py      # Entry point, doctor, main()
│   ├── source.py        # source add/list/remove/discover/test
│   ├── auth.py          # auth login/set/status/revoke
│   ├── sync.py          # sync run/status
│   ├── chat.py          # Interactive AI assistant
│   ├── docker.py        # Local Docker container management
│   └── helpers.py       # Shared utilities, resolve_for_provider()
├── connectors/          # API connectivity layer
│   ├── base.py          # Universal REST/GraphQL connector
│   ├── discovery.py     # HTTP probing, schema inference
│   ├── llm_discovery.py # Model-agnostic LLM layer (BYOK)
│   ├── registry.py      # Provider presets + name inference
│   └── self_healing.py  # Auth rotation + pagination recovery
├── auth/                # Credential storage
│   ├── credential_store.py  # OS keychain + encrypted file fallback
│   ├── device_flow.py       # OAuth 2.0 Device Authorization Flow
│   └── manager.py           # Auth coordinator
├── orchestrator/        # Pipeline execution
│   ├── pipeline.py      # DAG pipeline builder
│   ├── engine.py        # Async executor with retry
│   └── checkpoint.py    # Cursor-based checkpoint persistence
├── extractors/          # Orchestration-aware extraction
├── loaders/             # JSONL output (extensible)
├── schemas/             # Pydantic models
├── sources/             # Source state persistence
├── mcp/                 # Model Context Protocol server
└── observability/       # Structured logging + metrics
```

---

## Documentation

| Doc | What it covers |
|-----|---------------|
| [Tutorial: build your first pipeline](docs/tutorial.md) | End-to-end walkthrough for new users |
| [How to configure LLM providers](docs/how-to/configure-llm-providers.md) | BYOK setup: OpenAI, Anthropic, Ollama, Azure, Groq |
| [How to connect any API](docs/how-to/connect-any-api.md) | Known providers, unknown APIs, Docker, credentials |
| [How to add a new LLM provider](docs/how-to/add-llm-provider.md) | Contributor guide for extending the LLM layer |
| [Reference](docs/reference.md) | All env vars, CLI commands, provider presets, interfaces |
| [Architecture](docs/explanation.md) | How discovery, self-healing, and BYOK work under the hood |

---

## Development

```bash
git clone https://github.com/Lanrey/zero-to-pipeline.git
cd zero-to-pipeline
uv sync --extra dev

pytest tests/ -v          # 79 tests
ruff check src/data_pipeline/   # zero lint errors
mypy src/data_pipeline/         # type check
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full style guide (PEP 8) and pre-submit checklist.

---

## License

MIT