"""Provider registry for known API configurations.

DEMO ACCELERATORS — NOT REQUIREMENTS
======================================
Provider presets exist to make the live demo fast. The system is designed
to work with ANY API — unknown providers are discovered
dynamically via LLM-driven probing or inferred from the provider name.
Presets are NOT required for the system to function.

The primary use-case is Data Engineering & MLOps:
  pipeline source add mlflow            # experiment tracking
  pipeline source add wandb             # W&B runs & metrics
  pipeline source add your-feature-store  # any internal API

This is NOT a registry of hardcoded connectors. It stores known API
patterns (base URLs, auth styles, pagination) that accelerate discovery.
Unknown providers fall through to LLM-driven or name-based inference.
"""

from __future__ import annotations

from data_pipeline.schemas import AuthType, InferredConfig


class ProviderPreset:
    """A preset configuration for a known provider.

    These are hints, not hard dependencies. The system works without them —
    they just make the "zero config" experience faster for popular APIs.
    """

    def __init__(
        self,
        name: str,
        base_url: str,
        auth_type: AuthType = AuthType.API_KEY,
        *,
        auth_header: str = "Authorization",
        auth_prefix: str = "Bearer",
        pagination_style: str = "offset",
        api_style: str = "rest",
        rate_limit_rpm: int | None = None,
        default_headers: dict[str, str] | None = None,
        default_endpoints: dict[str, str] | None = None,
        health_endpoint: str = "/",
        docs_url: str | None = None,
        tagline: str | None = None,
    ):
        self.name = name
        self.base_url = base_url
        self.auth_type = auth_type
        self.auth_header = auth_header
        self.auth_prefix = auth_prefix
        self.pagination_style = pagination_style
        self.api_style = api_style
        self.rate_limit_rpm = rate_limit_rpm
        self.default_headers = default_headers or {}
        self.default_endpoints = default_endpoints or {}
        self.health_endpoint = health_endpoint
        self.docs_url = docs_url
        self.tagline = tagline


class ProviderRegistry:
    """Registry of known provider patterns for accelerated discovery.

    When a user says "add mlflow as a source", the registry provides the
    base URL and auth style instantly. For unknown providers, the system
    falls back to LLM-driven API discovery or name-based inference.

    Presets are demo accelerators — the system works without them.
    """

    def __init__(self):
        self._presets: dict[str, ProviderPreset] = {}

    def register(self, provider: str, preset: ProviderPreset) -> None:
        self._presets[provider.lower()] = preset

    def get_preset(self, provider: str) -> ProviderPreset | None:
        return self._presets.get(provider.lower())

    def has_preset(self, provider: str) -> bool:
        return provider.lower() in self._presets

    def infer_config(self, provider: str) -> InferredConfig:
        """Infer connection config from a provider name.

        For known providers: returns preset config instantly.
        For unknown providers: infers reasonable defaults from the name.
        The caller can then enrich this with LLM discovery.

        Returns an InferredConfig pydantic model (attribute access, not dict).
        """
        preset = self.get_preset(provider)
        if preset:
            return InferredConfig(
                name=preset.name,
                provider=provider.lower(),
                base_url=preset.base_url,
                auth_type=preset.auth_type.value,
                auth_header=preset.auth_header,
                auth_prefix=preset.auth_prefix,
                pagination_style=preset.pagination_style,
                api_style=preset.api_style,
                rate_limit_rpm=preset.rate_limit_rpm,
                default_headers=preset.default_headers,
                default_endpoints=preset.default_endpoints,
                health_endpoint=preset.health_endpoint,
                docs_url=preset.docs_url,
                source="preset",
            )

        return InferredConfig(
            name=provider.title(),
            provider=provider.lower(),
            base_url=f"https://api.{provider.lower()}.com",
            auth_type="bearer",
            auth_header="Authorization",
            auth_prefix="Bearer",
            pagination_style="unknown",
            api_style="rest",
            rate_limit_rpm=None,
            default_headers={},
            default_endpoints={},
            health_endpoint="/",
            docs_url=None,
            source="inferred",
        )

    @property
    def known_providers(self) -> list[str]:
        return sorted(self._presets.keys())


provider_registry = ProviderRegistry()

# =============================================================================
# DEMO ACCELERATOR PRESETS
# Primary focus: Data Engineering & MLOps tooling
# These make the live demo fast — the system works without them.
# =============================================================================

# ── ML Experiment Tracking ────────────────────────────────────────────────────

provider_registry.register("mlflow", ProviderPreset(
    name="MLflow",
    base_url="http://127.0.0.1:5001",  # local demo server (no auth) — port 5000 is macOS AirPlay
    auth_type=AuthType.NONE,
    auth_header="Authorization",
    auth_prefix="",
    pagination_style="offset",
    api_style="rest",
    default_endpoints={
        "runs": "/api/2.0/mlflow/runs/search",
        "experiments": "/api/2.0/mlflow/experiments/search",
        "models": "/api/2.0/mlflow/registered-models/search",
    },
    health_endpoint="/",
    docs_url="https://mlflow.org/docs/latest/rest-api.html",
    tagline="Open-source ML lifecycle management",
))

provider_registry.register("wandb", ProviderPreset(
    name="Weights & Biases",
    base_url="https://api.wandb.ai",
    auth_type=AuthType.API_KEY,
    auth_header="Authorization",
    auth_prefix="Bearer",
    pagination_style="cursor",
    api_style="rest",
    rate_limit_rpm=300,
    default_endpoints={
        "runs": "/graphql",
        "artifacts": "/graphql",
        "sweeps": "/graphql",
    },
    health_endpoint="/graphql",
    docs_url="https://docs.wandb.ai/ref/public-api/api",
    tagline="ML experiment tracking and model registry",
))

# ── Feature Stores ────────────────────────────────────────────────────────────

provider_registry.register("feast", ProviderPreset(
    name="Feast",
    base_url="http://127.0.0.1:6566",  # local feature server; override with --base-url
    auth_type=AuthType.NONE,
    auth_header="Authorization",
    auth_prefix="",
    pagination_style="offset",
    api_style="rest",
    default_endpoints={
        "features": "/get-online-features",
    },
    health_endpoint="/docs",
    docs_url="https://docs.feast.dev",
    tagline="Open-source feature store for ML",
))

# ── Data Observability & Monitoring ───────────────────────────────────────────

provider_registry.register("prometheus", ProviderPreset(
    name="Prometheus",
    base_url="http://localhost:9090",  # self-hosted; override with --base-url
    auth_type=AuthType.NONE,
    auth_header="Authorization",
    auth_prefix="Bearer",
    pagination_style="offset",
    api_style="rest",
    default_endpoints={
        "metrics": "/api/v1/query",
        "series": "/api/v1/series",
        "alerts": "/api/v1/alerts",
        "targets": "/api/v1/targets",
    },
    health_endpoint="/api/v1/status/buildinfo",
    docs_url="https://prometheus.io/docs/prometheus/latest/querying/api/",
    tagline="Metrics monitoring and alerting",
))

provider_registry.register("grafana", ProviderPreset(
    name="Grafana",
    base_url="http://localhost:3000",  # self-hosted; override with --base-url
    auth_type=AuthType.API_KEY,
    auth_header="Authorization",
    auth_prefix="Bearer",
    pagination_style="offset",
    api_style="rest",
    default_endpoints={
        "dashboards": "/api/search",
        "datasources": "/api/datasources",
        "alerts": "/api/alerts",
    },
    health_endpoint="/api/health",
    docs_url="https://grafana.com/docs/grafana/latest/developers/http_api/",
    tagline="Observability and dashboards",
))

# ── Data Pipeline & Orchestration ─────────────────────────────────────────────

provider_registry.register("airflow", ProviderPreset(
    name="Apache Airflow",
    base_url="http://localhost:8080",  # self-hosted; override with --base-url
    auth_type=AuthType.BASIC,
    auth_header="Authorization",
    auth_prefix="Basic",
    pagination_style="offset",
    api_style="rest",
    default_endpoints={
        "dags": "/api/v1/dags",
        "dag_runs": "/api/v1/dags/~/dagRuns",
        "task_instances": "/api/v1/dags/~/dagRuns/~/taskInstances",
    },
    health_endpoint="/api/v1/health",
    docs_url="https://airflow.apache.org/docs/apache-airflow/stable/stable-rest-api-ref.html",
    tagline="Workflow orchestration platform",
))

provider_registry.register("prefect", ProviderPreset(
    name="Prefect",
    base_url="https://api.prefect.cloud/api",
    auth_type=AuthType.API_KEY,
    auth_header="Authorization",
    auth_prefix="Bearer",
    pagination_style="offset",
    api_style="rest",
    rate_limit_rpm=600,
    default_endpoints={
        "flow_runs": "/flow_runs/filter",
        "flows": "/flows/filter",
        "deployments": "/deployments/filter",
    },
    health_endpoint="/health",
    docs_url="https://docs.prefect.io/api-ref/rest-api/",
    tagline="Modern dataflow automation",
))

# ── Version Control / Code Collaboration ──────────────────────────────────────

provider_registry.register("github", ProviderPreset(
    name="GitHub",
    base_url="https://api.github.com",
    auth_type=AuthType.OAUTH2,
    auth_header="Authorization",
    auth_prefix="Bearer",
    pagination_style="link_header",
    api_style="rest",
    rate_limit_rpm=5000,
    default_headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    },
    default_endpoints={
        "repositories": "/user/repos",
        "issues": "/repos/{owner}/{repo}/issues",
        "pull_requests": "/repos/{owner}/{repo}/pulls",
        "actions": "/repos/{owner}/{repo}/actions/runs",
    },
    health_endpoint="/user",
    docs_url="https://docs.github.com/en/rest",
    tagline="Code hosting and CI/CD",
))

# ── Generic / Productivity (kept as catch-all demo examples) ──────────────────

provider_registry.register("linear", ProviderPreset(
    name="Linear",
    base_url="https://api.linear.app",
    auth_type=AuthType.API_KEY,
    auth_header="Authorization",
    auth_prefix="",
    pagination_style="graphql_cursor",
    api_style="graphql",
    rate_limit_rpm=1500,
    default_endpoints={
        "issues": "/graphql",
        "projects": "/graphql",
    },
    health_endpoint="/graphql",
    docs_url="https://linear.app/docs/graphql/working-with-the-graphql-api",
    tagline="Issue tracking and project management",
))

provider_registry.register("notion", ProviderPreset(
    name="Notion",
    base_url="https://api.notion.com",
    auth_type=AuthType.OAUTH2,
    auth_header="Authorization",
    auth_prefix="Bearer",
    pagination_style="cursor",
    api_style="rest",
    rate_limit_rpm=180,
    default_headers={"Notion-Version": "2022-06-28"},
    default_endpoints={
        "databases": "/v1/search",
        "pages": "/v1/search",
    },
    health_endpoint="/v1/users/me",
    docs_url="https://developers.notion.com/reference",
    tagline="Docs and knowledge base",
))
