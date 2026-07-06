# Contributing

## Setup

```bash
git clone <your-repo-url>
cd data-ingestion-pydata-helenski-demo
uv sync --project python --extra dev
```

## Run locally

```bash
# New production CLI
pipeline --help
pipeline source list
pipeline doctor

# Legacy CLI (still functional)
agentctl --help
```

## Testing

```bash
# Run all tests
uv run --project python --directory python pytest -v

# With coverage
uv run --project python --directory python pytest --cov=data_pipeline

# Lint
uv run --project python ruff check python/src/data_pipeline/

# Type check
uv run --project python mypy python/src/data_pipeline/
```

## Style

- Keep changes focused and minimal.
- Follow ruff's opinionated formatting (line length: 100).
- Prefer clear error messages over stack traces for user-facing errors.
- Update README with behavior changes.
- Add tests for new functionality.

## Architecture

The codebase is modular — each directory has a single responsibility:

| Module | Responsibility |
|--------|---------------|
| `auth/` | Credential storage (keyring) and OAuth flows |
| `connectors/` | Source-specific extraction logic |
| `orchestrator/` | Pipeline DAG execution and checkpointing |
| `extractors/` | Orchestration-aware extraction steps |
| `loaders/` | Data destinations (JSONL, etc.) |
| `observability/` | Structured logging and metrics |
| `mcp/` | MCP server for AI assistant integration |
| `schemas/` | Pydantic models shared across modules |
