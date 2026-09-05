# Zero to Pipeline

**Self-configuring data ingestion — connect any API without writing connectors.**

[![Python 3.10+](https://img.shields.io/pypi/pyversions/zero-to-pipeline)](https://pypi.org/project/zero-to-pipeline/)
[![PyPI version](https://img.shields.io/pypi/v/zero-to-pipeline)](https://pypi.org/project/zero-to-pipeline/)
[![License: MIT](https://img.shields.io/pypi/l/zero-to-pipeline)](https://opensource.org/licenses/MIT)
[![Tests](https://img.shields.io/github/actions/workflow/status/Lanrey/zero-to-pipeline/publish.yml?label=tests)](https://github.com/Lanrey/zero-to-pipeline/actions)

Zero to Pipeline is a Python framework that turns any REST or GraphQL API into a data source in a single command. No connector classes, no YAML schemas, no pagination boilerplate. The framework uses an LLM (your own API key) to discover auth type, endpoints, and pagination style from a provider name — then extracts data with self-healing, checkpointed syncs.

---

## Tutorial: Build your first pipeline

Connect to an API, store credentials, and extract data in under five minutes.

### Prerequisites

- Python 3.10+
- An API key for OpenAI or Anthropic (or a local Ollama instance)

### Step 1 — Install

```bash
pip install zero-to-pipeline
```

### Step 2 — Store your LLM API key

```bash
pipeline auth set openai --token sk-proj-...
```

### Step 3 — Add a data source

```bash
pipeline source add mlflow
```

### Step 4 — Test the connection

```bash
pipeline source test mlflow
```

You should see: `Connection to mlflow successful!`

### Step 5 — Extract data

```bash
pipeline sync run mlflow
```

The first run fetches all records. The second run resumes from the last cursor — only new records.

### What you learned

You connected to an API, stored credentials securely, and extracted paginated data — without writing a single line of connector code.

---

## How-to guides

| Task | Guide |
|------|-------|
| Configure LLM providers (OpenAI, Anthropic, Ollama, Azure, Groq) | [docs/how-to/configure-llm-providers.md](docs/how-to/configure-llm-providers.md) |
| Connect any API (known, unknown, Docker, credentials) | [docs/how-to/connect-any-api.md](docs/how-to/connect-any-api.md) |
| Add a new LLM provider to the framework | [docs/how-to/add-llm-provider.md](docs/how-to/add-llm-provider.md) |

---

## Explanation: How it works

Traditional ETL tools require you to write a connector for every API. Zero to Pipeline inverts the problem: **you say what to connect, and the framework figures out how.**

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

| Layer | Problem it solves |
|-------|-------------------|
| **Registry + LLM** | Discovers auth type and endpoints from a provider name |
| **Secure Auth** | Stores tokens in OS keychain, never plaintext |
| **Self-Healing** | Rotates auth header formats until one works |
| **Pagination** | Infers cursor/offset/GraphQL from response shape |
| **Orchestrator** | Checkpoints every batch, runs steps as a parallel DAG |

See [docs/explanation.md](docs/explanation.md) for the full architecture deep dive.

---

## Reference

### Supported providers

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

### Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `PIPELINE_LLM_PROVIDER` | `openai` | LLM provider: `openai` or `anthropic` |
| `PIPELINE_LLM_MODEL` | per-provider | Model ID |
| `PIPELINE_LLM_BASE_URL` | per-provider | Override for Azure, Ollama, Groq |
| `PIPELINE_LOG_LEVEL` | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `PIPELINE_DEFAULT_TIMEOUT` | `30` | HTTP request timeout in seconds |
| `PIPELINE_MAX_RETRIES` | `3` | Maximum retry attempts for failed requests |

All settings support `.env` files. See [docs/reference.md](docs/reference.md) for the complete list.

### CLI commands

| Command | Description |
|---------|-------------|
| `pipeline source add <provider>` | Add a data source |
| `pipeline source list` | List configured sources |
| `pipeline source test <provider>` | Test connection |
| `pipeline source remove <provider>` | Remove a source |
| `pipeline auth set <provider>` | Store API key in OS keychain |
| `pipeline auth status` | Show auth status |
| `pipeline sync run <provider>` | Extract data (incremental) |
| `pipeline sync status` | Show checkpoint info |
| `pipeline chat` | Interactive AI assistant |
| `pipeline doctor` | Health check |

### Project structure

```
src/data_pipeline/
├── cli/                 # CLI commands (package, not a single file)
├── connectors/          # API connectivity layer
├── auth/                # Credential storage
├── orchestrator/        # Pipeline execution
├── extractors/          # Orchestration-aware extraction
├── loaders/             # JSONL output (extensible)
├── schemas/             # Pydantic models
├── sources/             # Source state persistence
├── mcp/                 # Model Context Protocol server
└── observability/       # Structured logging + metrics
```

### Development

```bash
git clone https://github.com/Lanrey/zero-to-pipeline.git
cd zero-to-pipeline
uv sync --extra dev

pytest tests/ -v            # 79 tests
ruff check src/data_pipeline/   # zero lint errors
mypy src/data_pipeline/         # type check
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full style guide and pre-submit checklist.

---

## License

MIT