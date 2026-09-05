# How zero-pipeline works

zero-pipeline connects to any API without requiring you to write connector code. This page explains the design decisions behind the system.

## The problem

Traditional data pipeline frameworks require you to write a connector class for every API you want to ingest from. Each connector hard-codes the base URL, authentication format, pagination strategy, and response schema. Adding a new source means writing hundreds of lines of boilerplate.

zero-pipeline inverts this: you provide a provider name, and the system figures out the rest.

## Architecture layers

```
  "pipeline source add mlflow"
          │
          ▼
  ┌─────────────────────┐
  │  Provider Registry   │  Known presets (base URL, auth, pagination)
  └────────┬────────────┘
           │ enriches / discovers
           ▼
  ┌─────────────────────┐
  │   LLM Discovery     │  Asks an LLM for endpoints, rate limits, quirks
  └────────┬────────────┘
           │ validates
           ▼
  ┌─────────────────────┐
  │   HTTP Probing      │  Checks reachability, detects pagination from headers
  └────────┬────────────┘
           │ connects
           ▼
  ┌─────────────────────┐
  │  Self-Healing       │  Rotates auth formats on 401/403, resumes from cursor
  │  Connector          │
  └────────┬────────────┘
           │ extracts
           ▼
  ┌─────────────────────┐
  │  Checkpointed       │  Saves cursor every batch, incremental on next run
  │  Orchestrator       │
  └─────────────────────┘
```

## LLM-driven discovery

When you add a source, zero-pipeline runs a three-stage discovery:

1. **Registry lookup** — if the provider has a preset (MLflow, GitHub, Airflow, etc.), that gives us base URL, auth type, pagination style, and default endpoints instantly.

2. **LLM enrichment** — regardless of whether a preset exists, the system asks the configured LLM to discover (or enrich) the API configuration. For known providers, the LLM fills gaps the static preset can't capture: new endpoints, current rate limits, API quirks. For unknown providers, the LLM discovers the full configuration from scratch.

3. **HTTP probing** — if no LLM is available, the system probes the base URL directly: checks reachability, inspects response headers for rate-limit hints and pagination patterns, and looks for an OpenAPI spec.

The merge priority is: user overrides > preset > LLM > probe > name inference.

## BYOK model-agnostic LLM layer

The LLM layer is provider-agnostic. You bring your own API key for whichever provider you prefer.

Two provider implementations are included:

- **OpenAI-compatible** — sends requests to `/v1/chat/completions`. Works with OpenAI, Azure OpenAI, Groq, Together, Mistral, DeepSeek, OpenRouter, Ollama, vLLM, LM Studio, and any server implementing this format. This is the default.

- **Anthropic** — sends requests to `/v1/messages` using the native Anthropic Messages API with `x-api-key` authentication.

Both use raw `httpx` HTTP calls with no SDK dependencies. The provider is selected via the `PIPELINE_LLM_PROVIDER` env var and the API key is resolved from the OS keychain (preferred) or a well-known env var (fallback for CI).

Local inference servers (Ollama, vLLM) are supported without any API key: set `PIPELINE_LLM_BASE_URL` to the server address and the `Authorization` header is omitted automatically.

## Self-healing connector

The `SelfHealingConnector` wraps the base `APIConnector` with automatic failure recovery:

- **Auth format rotation** — when a request returns 401/403, the connector tries different auth header formats (Bearer, raw token, X-API-Key, Api-Key, Basic) until one succeeds. The winning format is saved for all subsequent requests.

- **Pagination recovery** — if auth fails mid-pagination, the connector heals the auth format, then resumes extraction from the last successfully yielded cursor. Records already yielded are deduplicated.

- **No-auth passthrough** — for sources configured without credentials (MLflow, Prometheus), auth healing is skipped entirely. A 401/403 is surfaced as a plain error with instructions to set credentials.

## Credential storage

API keys are stored in the OS keychain via the `keyring` library:

- **macOS**: Keychain Access
- **Linux**: Secret Service (GNOME Keyring, KWallet)
- **CI**: Falls back to env vars (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`)

Keys are never written to disk, `.env` files, or config files. The `pipeline auth set` command stores them; `pipeline auth revoke` removes them.

## Checkpointed extraction

Every sync saves a cursor checkpoint after each batch of records. On the next run:

1. The checkpoint is loaded
2. Extraction resumes from the saved cursor
3. Only new records are fetched

This means a failed sync can be restarted without re-fetching everything, and regular syncs are incremental by default. Use `--full` to force a complete re-sync.

## Pagination strategies

The connector supports four pagination strategies, automatically selected based on the API response:

| Strategy | Detection signal | Examples |
|----------|-----------------|----------|
| **Offset** | Default fallback | Most REST APIs |
| **Cursor** | `next_cursor`, `has_more` in response body | Notion, Slack |
| **Link header** | `Link: <url>; rel="next"` in response headers | GitHub |
| **GraphQL cursor** | `pageInfo.hasNextPage` + `endCursor` in response | Linear, W&B |
| **MLflow-specific** | `next_page_token` in body, POST with `page_token` | MLflow runs/search |

## See also

- [Tutorial: build your first pipeline](tutorial.md)
- [How to configure LLM providers](how-to/configure-llm-providers.md)
- [Reference: configuration options](reference.md)

---

### Validation checklist

**Pre-hook (before writing):**
- [ ] Is the concept framed as something to *understand*, not something to *do*?
- [ ] Does it answer *why* or *how does X work* rather than *how do I X*?

**Post-hook (after writing):**
- [ ] Can a reader explain the concept in their own words after reading?
- [ ] Are there no step-by-step instructions or technical specs mixed in?
