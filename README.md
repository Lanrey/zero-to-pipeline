# Zero to Pipeline

Published PyPI framework that turns any REST or GraphQL API into a data source in one CLI command — the LLM discovers auth, endpoints, and pagination automatically. Data engineers use it to skip hours of connector boilerplate. Written in Python with Typer, Pydantic, and httpx.

**Stack:** Python 3.10+ · Typer (CLI) · Pydantic v2 · httpx · structlog · keyring · tenacity · PyPI

## What

A published Python framework (`pip install zero-to-pipeline`) that turns any REST or GraphQL API into a data source in a single CLI command — the LLM discovers auth type, endpoints, and pagination style from just the provider name, with no connector classes, YAML, or boilerplate.

## Why

Traditional ETL frameworks require a hand-written connector for every API — a weeks-long process of reading docs, implementing auth, handling pagination, and writing retry logic. Zero to Pipeline inverts the model: describe the source, let the LLM do the discovery.

## How

`pipeline source add <provider>` sends the provider name to an LLM (OpenAI, Anthropic, Ollama, Azure, or Groq) which probes endpoints, infers auth type (OAuth, API key, none), identifies pagination strategy (cursor, offset, page_token), and writes a config. `pipeline sync run <provider>` extracts data with automatic cursor checkpointing, self-healing on auth failures, and parallelisable DAG execution. Credentials are stored securely via `keyring`.

## Impact

Reduces API connector setup from hours/days to under 5 minutes for any REST/GraphQL API. Published on PyPI with full docs; actively used as the centrepiece of a EuroPython 2026 and PyData Helsinki talk. First-party authored.

## Use Cases

- `pipeline source add mlflow` — connected, pagination auto-discovered, 13 runs synced in one command
- Second sync run fetches only 3 new records using the saved cursor — no re-fetching historical data
- Connect a private internal API by pasting its OpenAPI spec; the LLM infers the connector config from the spec