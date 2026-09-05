# AGENTS.md

## Tooling

- Use `uv`, not `pip` or `pip install`. All commands run via `uv run` or `uv sync`.
- Commands run from the repo root (no more `cd python/`).

## Testing

- Tests are async (`asyncio_mode = "auto"` in pyproject.toml) — new test files don't need `@pytest.mark.asyncio` on every test, but they do need `async def`.
- Run tests: `pytest tests/ -v`
- Lint: `ruff check src/data_pipeline/`
- Type check: `mypy src/data_pipeline/`

## Landmines

- Only `ANTHROPIC_API_KEY` is needed for LLM-powered source discovery. Other env vars (Google, Slack, Microsoft OAuth) are provider-specific and not required by the core library.

## Source layout

- Library code lives in `src/data_pipeline/` (src-layout via `package-dir = {"" = "src"}` in pyproject.toml).
- `feast`, `pandas`, `scikit-learn`, `mlflow` are not dependencies — the core library is a general data-pipeline toolkit with no ML opinions.
- The CLI is a package, not a single file: `src/data_pipeline/cli/__init__.py` is the entry point; commands are in `source.py`, `auth.py`, `sync.py`, `chat.py`, with shared utilities in `helpers.py` and Docker management in `docker.py`.
- `cli/helpers.py` provides `resolve_for_provider()` — the single entry point for turning a provider name into a `ResolvedConnection` (used by both `source_test` and `sync_run`).
