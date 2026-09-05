"""Global configuration and settings management."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class PipelineSettings(BaseSettings):
    model_config = {"env_prefix": "PIPELINE_", "env_file": ".env", "extra": "ignore"}

    workspace_dir: Path = Field(
        default_factory=lambda: Path.home() / ".zero-pipeline" / "workspaces" / "default"
    )
    log_level: str = "INFO"
    log_format: str = "json"

    default_timeout: int = 30
    max_retries: int = 3
    retry_base_delay: float = 1.0
    retry_max_delay: float = 60.0

    checkpoint_dir: Path = Field(
        default_factory=lambda: Path.home() / ".zero-pipeline" / "checkpoints"
    )

    keyring_service: str = "zero-pipeline"

    llm_provider: str = "openai"
    llm_model: str = ""
    llm_base_url: str = ""


settings = PipelineSettings()
