# Build your first data pipeline

Connect to an API, store credentials securely, and extract data in under five minutes.

## Prerequisites

- Python 3.11+
- A terminal
- An API key for your LLM provider (OpenAI, Anthropic, or any OpenAI-compatible service)

## Step 1 — Install zero-pipeline

```bash
pip install zero-pipeline
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv add zero-pipeline
```

Verify the installation:

```bash
pipeline doctor
```

You should see all checks passing:

```
  OK  Keyring accessible
  OK  Provider registry loaded
  OK  Checkpoint dir writable
  OK  Source store accessible

All checks passed!
```

## Step 2 — Configure your LLM provider

zero-pipeline uses an LLM to discover API configurations for data sources you connect. You need to provide your own API key.

**Option A — OpenAI (default)**

```bash
pipeline auth set openai --token sk-proj-your-key-here
```

**Option B — Anthropic**

```bash
export PIPELINE_LLM_PROVIDER=anthropic
pipeline auth set anthropic --token sk-ant-your-key-here
```

**Option C — Local model (Ollama, no key needed)**

```bash
export PIPELINE_LLM_PROVIDER=openai
export PIPELINE_LLM_BASE_URL=http://localhost:11434
export PIPELINE_LLM_MODEL=llama3.2
```

Your API keys are stored in the OS keychain (macOS Keychain / Linux Secret Service), never written to disk.

## Step 3 — Add a data source

Add MLflow as a data source with a local Docker instance:

```bash
pipeline source add mlflow --local
```

You should see:

```
Setting up mlflow locally
  image: ghcr.io/mlflow/mlflow
  port:  5000
  → Pulling ghcr.io/mlflow/mlflow...
  → Starting zero-pipeline-mlflow on port 5000...
  ✓ mlflow is ready at http://127.0.0.1:5000

Source 'MLflow' added (from: preset+llm)
  Base URL: http://127.0.0.1:5000
  Auth: none
  Pagination: offset
```

No Docker? Add any API source manually:

```bash
pipeline source add github
```

## Step 4 — Store credentials (if needed)

If your source requires authentication:

```bash
pipeline auth set github --token ghp_your-token-here
```

Check what's configured:

```bash
pipeline auth status
```

```
         Auth Status
┌────────────┬──────────┬───────────────────┐
│ Provider   │ Auth type│ Status            │
├────────────┼──────────┼───────────────────┤
│ github     │ oauth2   │ authenticated     │
│ mlflow     │ none     │ no auth required  │
└────────────┴──────────┴───────────────────┘
```

## Step 5 — Test the connection

```bash
pipeline source test mlflow
```

```
Connection to mlflow successful!
```

## Step 6 — Extract data

```bash
pipeline sync run mlflow
```

```
  source:      mlflow
  url:         http://127.0.0.1:5000
  endpoint:    /api/2.0/mlflow/runs/search
  auth:        none (no credentials)

Syncing mlflow /api/2.0/mlflow/runs/search...
  1. run_abc123
  2. run_def456
  3. run_ghi789

Done: 13 records extracted
  output:     ~/.zero-pipeline/output/mlflow/search.jsonl
  checkpoint: saved — next run resumes from cursor, not from zero
```

Run it again — it picks up only new records:

```bash
pipeline sync run mlflow
# → 3 new records (not 13)
```

## Step 7 — Explore interactively

Start the AI assistant:

```bash
pipeline chat
```

```
Zero-to-Pipeline Assistant  (type 'exit' or Ctrl-C to quit)

You: add prometheus --local
```

The assistant understands natural language and executes pipeline commands for you.

## What's next

- [How to configure LLM providers](how-to/configure-llm-providers.md) — switch models, use local inference, point at Azure or Groq
- [How to connect any API](how-to/connect-any-api.md) — internal APIs, custom feature stores, unknown providers
- [Reference: configuration options](reference.md) — all env vars, CLI commands, and settings
- [Architecture: how zero-pipeline works](explanation.md) — LLM discovery, self-healing, and the connector model

---

### Validation checklist

Use this when updating this tutorial:

**Pre-hook (before writing):**
- [ ] Is the goal stated in the first paragraph as a single, beginner-achievable outcome?
- [ ] Are all prerequisites listed and minimal (Python, terminal, API key)?
- [ ] Can every step be verified independently with a visible result?

**Post-hook (after writing):**
- [ ] Can a beginner complete every step end-to-end without external help?
- [ ] Does each numbered step produce a testable output (CLI output, file, status message)?
- [ ] Are there no explanatory digressions that belong in a how-to guide or explanation?
