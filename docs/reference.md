# Reference

Technical reference for zero-pipeline configuration, CLI commands, and internals.

## LLM provider settings

All settings use the `PIPELINE_` env var prefix. They can also be set in a `.env` file at the project root.

| Env var | Type | Default | Description |
|---------|------|---------|-------------|
| `PIPELINE_LLM_PROVIDER` | string | `openai` | LLM provider to use. Values: `openai`, `anthropic` |
| `PIPELINE_LLM_MODEL` | string | *(per-provider)* | Model ID. Defaults: `gpt-4o` (openai), `claude-sonnet-4-20250514` (anthropic) |
| `PIPELINE_LLM_BASE_URL` | string | *(per-provider)* | API base URL. Defaults: `https://api.openai.com` (openai), `https://api.anthropic.com` (anthropic). Override for Azure, Groq, Ollama, vLLM, etc. |

### API key resolution

Keys are resolved in this order:

1. **OS keychain** via `pipeline auth set <provider>` (stored under service `zero-pipeline`)
2. **Env var fallback**: `OPENAI_API_KEY` for the `openai` provider, `ANTHROPIC_API_KEY` for `anthropic`

The keychain is preferred. Env vars exist as a convenience for CI environments.

### Local inference (no key)

When `PIPELINE_LLM_BASE_URL` is set to a non-default URL and no API key is configured, the provider is still considered available. The `Authorization` header is omitted entirely. This supports Ollama, vLLM, LM Studio, and similar local servers.

## General settings

| Env var | Type | Default | Description |
|---------|------|---------|-------------|
| `PIPELINE_WORKSPACE_DIR` | path | `~/.zero-pipeline/workspaces/default` | Base directory for workspace data |
| `PIPELINE_LOG_LEVEL` | string | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `PIPELINE_LOG_FORMAT` | string | `json` | Log format: `json` or `console` |
| `PIPELINE_DEFAULT_TIMEOUT` | int | `30` | HTTP request timeout in seconds |
| `PIPELINE_MAX_RETRIES` | int | `3` | Maximum retry attempts for failed requests |
| `PIPELINE_RETRY_BASE_DELAY` | float | `1.0` | Base delay for exponential backoff (seconds) |
| `PIPELINE_RETRY_MAX_DELAY` | float | `60.0` | Maximum backoff delay (seconds) |
| `PIPELINE_CHECKPOINT_DIR` | path | `~/.zero-pipeline/checkpoints` | Directory for sync checkpoint files |
| `PIPELINE_KEYRING_SERVICE` | string | `zero-pipeline` | OS keychain service name |

## CLI commands

### `pipeline source`

| Command | Description |
|---------|-------------|
| `pipeline source add <provider>` | Add a data source. Flags: `--base-url`, `--auth-type`, `--local`, `--force` |
| `pipeline source list` | List all added data sources |
| `pipeline source list-providers` | List known provider presets |
| `pipeline source test <provider>` | Test connection to a source. Flag: `--base-url` |
| `pipeline source discover <provider>` | Probe an API to discover endpoints. Flag: `--base-url` |
| `pipeline source remove <provider>` | Remove a source. Flag: `--force` |

### `pipeline auth`

| Command | Description |
|---------|-------------|
| `pipeline auth set <provider>` | Store an API key in the OS keychain. Flag: `--token` |
| `pipeline auth login <provider>` | OAuth Device Flow authentication (not yet implemented for most providers) |
| `pipeline auth status` | Show authentication status for all sources |
| `pipeline auth revoke <provider>` | Remove stored credentials |

### `pipeline sync`

| Command | Description |
|---------|-------------|
| `pipeline sync run <provider> [path]` | Extract data from an API endpoint. Flags: `--base-url`, `--full` |
| `pipeline sync status` | Show checkpoint info for active syncs |

### `pipeline chat`

```bash
pipeline chat                    # interactive REPL
pipeline chat "add mlflow"       # one-shot message
```

### `pipeline doctor`

Runs health checks: keyring access, registry, checkpoint directory, source store.

## Provider presets

These are demo accelerators. The system works without them.

| Provider | Base URL | Auth | Pagination | Category |
|----------|----------|------|------------|----------|
| `mlflow` | `http://127.0.0.1:5001` | none | offset | ML experiment tracking |
| `wandb` | `https://api.wandb.ai` | api_key | cursor | ML experiment tracking |
| `feast` | `http://127.0.0.1:6566` | none | offset | Feature store |
| `prometheus` | `http://localhost:9090` | none | offset | Data observability |
| `grafana` | `http://localhost:3000` | api_key | offset | Data observability |
| `airflow` | `http://localhost:8080` | basic | offset | Pipeline orchestration |
| `prefect` | `https://api.prefect.cloud/api` | api_key | offset | Pipeline orchestration |
| `github` | `https://api.github.com` | oauth2 | link_header | Version control |
| `linear` | `https://api.linear.app` | api_key | graphql_cursor | Issue tracking |
| `notion` | `https://api.notion.com` | oauth2 | cursor | Knowledge base |

## LLM provider interface

For contributors adding new providers. Implement the `LLMProvider` ABC from `data_pipeline.connectors.llm_discovery`:

```python
class LLMProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def complete(self, prompt: str, *, timeout: int = 30) -> LLMResult: ...

    def chat(
        self,
        messages: list[dict],
        *,
        system: str,
        max_tokens: int = 2048,
        timeout: int = 60,
    ) -> LLMResult:
        """Default: extracts last user message and calls complete()."""
        ...
```

### `LLMResult`

```python
@dataclass(slots=True)
class LLMResult:
    text: str                           # raw response text
    parsed: dict[str, Any] | None       # auto-parsed JSON (if valid)
    success: bool = True
    error: str | None = None
```

### Registering a new provider

Add the class to the `PROVIDERS` dict in `llm_discovery.py`:

```python
PROVIDERS: dict[str, type[LLMProvider]] = {
    "openai": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
    "my_provider": MyProvider,  # add here
}
```

Users select it with `PIPELINE_LLM_PROVIDER=my_provider`.

## File locations

| Path | Contents |
|------|----------|
| `~/.zero-pipeline/sources/<slug>/config.json` | Persisted source configuration |
| `~/.zero-pipeline/checkpoints/` | Sync checkpoint files (cursor state) |
| `~/.zero-pipeline/output/<provider>/` | Extracted JSONL data files |
| `~/.zero-pipeline/workspaces/default/` | Default workspace directory |
