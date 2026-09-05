# Changelog

## 1.0.0 (2026-09-05)

Initial release.

### Features

- LLM-discovered API connectors — give it a provider name, it figures out auth type, endpoints, and pagination style
- Self-healing auth — rotates header formats (Bearer, token, X-API-Key, Basic) on 401/403
- Automatic pagination detection — cursor, offset, link-header, and GraphQL
- Checkpointed extraction — saves cursor every 100 records; incremental syncs resume from cursor
- Secure credential storage — OS keychain via `keyring`, encrypted file fallback
- OAuth 2.0 Device Authorization Flow (RFC 8628)
- DAG pipeline orchestrator with async parallel execution and retry
- Provider presets: MLflow, Weights & Biases, Feast, Prometheus, Grafana, Airflow, Prefect, GitHub, Linear, Notion
- LLM provider support: OpenAI-compatible (OpenAI, Azure, Groq, Together, Ollama, vLLM, LM Studio) and Anthropic
- CLI with smart command suggestions on typos
- Interactive AI assistant REPL (`pipeline chat`)
- MCP (Model Context Protocol) server for AI tool integration
- Docker auto-provisioning for local dev instances (`--local` flag)
- Structured logging (structlog) and metrics collector
- `pip install zero-to-pipeline` — single-command install, zero YAML or connector code needed