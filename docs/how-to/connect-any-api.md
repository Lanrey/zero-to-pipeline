# How to connect any API

zero-pipeline connects to any REST or GraphQL API. Known providers (MLflow, GitHub, Airflow, etc.) have preset configurations; unknown providers are discovered by the LLM or inferred from the name.

## Add a known provider

```bash
pipeline source add github
```

The registry supplies the base URL, auth type, pagination style, and default endpoints. The LLM enriches this with deeper knowledge.

See all known presets:

```bash
pipeline source list-providers
```

## Add an unknown provider

```bash
pipeline source add my-feature-store --base-url https://features.internal.co
```

For unknown providers, zero-pipeline:

1. Infers a default base URL from the name (e.g. `https://api.my-feature-store.com`)
2. Asks the LLM to discover auth type, pagination style, and endpoints
3. Falls back to HTTP probing if the LLM is unavailable

Always pass `--base-url` for internal APIs where the name-based URL won't work.

## Override configuration

```bash
pipeline source add airflow \
    --base-url http://airflow.internal:8080 \
    --auth-type basic
```

User-supplied flags always override the registry preset and LLM discovery.

## Start a local instance with Docker

Supported providers can be started locally with `--local`:

```bash
pipeline source add mlflow --local
pipeline source add prometheus --local
pipeline source add grafana --local
```

This pulls the Docker image, starts the container, waits for the health endpoint, and adds the source with the correct base URL.

## Store credentials

For sources that require authentication:

```bash
pipeline auth set github --token ghp_...
```

The CLI shows where to find your credentials before prompting:

```
Where to find your github credentials:
  Docs:         https://docs.github.com/en/rest
  Auth type:    oauth2
  How to get it: Create a personal access token at github.com/settings/tokens
```

## Test the connection

```bash
pipeline source test github
```

## Sync data from a specific endpoint

```bash
# Use the source's default endpoint
pipeline sync run github

# Specify an endpoint explicitly
pipeline sync run github /repos/your-org/your-repo/issues
```

## Force a full re-sync

By default, syncs are incremental (resuming from the last checkpoint):

```bash
# Incremental (default)
pipeline sync run github

# Full re-sync, ignoring checkpoints
pipeline sync run github --full
```

## Remove a source

```bash
pipeline source remove github
```

## See also

- [Reference: CLI commands](../reference.md#cli-commands)
- [Reference: provider presets](../reference.md#provider-presets)
- [Architecture: how discovery works](../explanation.md#llm-driven-discovery)

---

### Validation checklist

**Pre-hook (before writing):**
- [ ] Is the task framed as a problem to solve (not a learning exercise)?
- [ ] Are assumptions and prerequisite knowledge stated up front?

**Post-hook (after writing):**
- [ ] Can an experienced user complete the task without confusion?
- [ ] Are there no conceptual digressions that belong in an explanation?
