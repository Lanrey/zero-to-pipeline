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

## Configuration

The CLI stores source configs and checkpoints locally. Set `ANTHROPIC_API_KEY` for LLM-powered source discovery:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

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

## License

MIT
