# zero-pipeline

Self-configuring data ingestion framework. Connect to any API source without writing connector classes, YAML, or pagination logic.

## What it does

- **LLM-discovered connectors** — give it a provider name, it figures out the auth type, endpoints, and pagination style
- **Self-healing auth** — rotates header formats on 403s, surfaces docs URLs before prompting for credentials
- **Automatic pagination** — detects cursor, offset, and GraphQL patterns from API responses
- **Checkpointed orchestration** — saves cursor state after every batch; incremental syncs resume where they left off
- **Secure credential storage** — tokens go to the OS keychain, never to disk or `.env` files

## Install

```bash
pip install zero-pipeline
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv add zero-pipeline
```

Requires Python 3.11+.

## Quick start

```bash
# Check everything is working
pipeline doctor

# Add a source (LLM discovers auth, endpoints, pagination)
pipeline source add <provider-name>

# Test the connection
pipeline source test <provider-name>

# Sync data (first run fetches everything, subsequent runs are incremental)
pipeline sync run <provider-name>

# See what's connected
pipeline source list
pipeline auth status
```

## Example: adding an API source

```bash
# Add with explicit config
pipeline source add mlflow \
    --base-url http://127.0.0.1:5001 \
    --auth-type none

# First sync: fetches all records, saves cursor
pipeline sync run mlflow
# → 13 records extracted, checkpoint saved

# Second sync: resumes from cursor
pipeline sync run mlflow
# → 3 new records (not 13)
```

## How it works

| Layer | Problem it solves |
|-------|-------------------|
| **Registry + LLM** | Discovers auth type and endpoints from a provider name |
| **Secure Auth** | Stores tokens in OS keychain, never plaintext |
| **Self-Healing** | Rotates auth header formats until one works |
| **Pagination** | Infers cursor/offset/GraphQL from response shape |
| **Orchestrator** | Checkpoints every batch, runs steps as a parallel DAG |

## LLM provider (BYOK)

zero-pipeline uses an LLM to discover API configurations. Bring your own key for any supported provider:

```bash
# Option A: OpenAI (default)
pipeline auth set openai --token sk-proj-...

# Option B: Anthropic
export PIPELINE_LLM_PROVIDER=anthropic
pipeline auth set anthropic --token sk-ant-...

# Option C: Local model (Ollama, vLLM) — no key needed
export PIPELINE_LLM_BASE_URL=http://localhost:11434
export PIPELINE_LLM_MODEL=llama3.2
```

The OpenAI-compatible provider works with OpenAI, Azure, Groq, Together, Mistral, DeepSeek, OpenRouter, Ollama, vLLM, and LM Studio. Keys are stored in the OS keychain, never on disk.

See [docs/how-to/configure-llm-providers.md](docs/how-to/configure-llm-providers.md) for full setup options.

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `PIPELINE_LLM_PROVIDER` | `openai` | LLM provider: `openai` or `anthropic` |
| `PIPELINE_LLM_MODEL` | per-provider | Model ID (e.g. `gpt-4o`, `claude-sonnet-4-20250514`) |
| `PIPELINE_LLM_BASE_URL` | per-provider | Override for Azure, Ollama, Groq, etc. |

All settings support `.env` files. See [docs/reference.md](docs/reference.md) for the complete list.

## Development

```bash
git clone https://github.com/Lanrey/zero-to-pipeline.git
cd zero-to-pipeline
uv sync --extra dev

# Tests
pytest tests/ -v

# Lint
ruff check src/data_pipeline/

# Type check
mypy src/data_pipeline/
```

## Documentation

| Doc | What it covers |
|-----|---------------|
| [Tutorial: build your first pipeline](docs/tutorial.md) | End-to-end walkthrough for new users |
| [How to configure LLM providers](docs/how-to/configure-llm-providers.md) | BYOK setup: OpenAI, Anthropic, Ollama, Azure, Groq |
| [How to connect any API](docs/how-to/connect-any-api.md) | Known providers, unknown APIs, Docker, credentials |
| [How to add a new LLM provider](docs/how-to/add-llm-provider.md) | Contributor guide for extending the LLM layer |
| [Reference](docs/reference.md) | All env vars, CLI commands, provider presets, interfaces |
| [Architecture](docs/explanation.md) | How discovery, self-healing, and BYOK work under the hood |

## License

MIT
