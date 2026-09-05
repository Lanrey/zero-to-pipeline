"""Pipeline execution models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SyncMode(str, Enum):
    FULL = "full"
    INCREMENTAL = "incremental"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class StepResult(BaseModel):
    step_name: str
    status: StepStatus
    records_processed: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PipelineResult(BaseModel):
    pipeline_id: str
    source_id: str
    sync_mode: SyncMode
    status: StepStatus
    steps: list[StepResult] = Field(default_factory=list)
    total_records: int = 0
    started_at: datetime
    completed_at: datetime | None = None
    error: str | None = None


class CheckpointState(BaseModel):
    pipeline_id: str
    source_id: str
    cursor: str | None = None
    last_sync_at: datetime | None = None
    last_record_id: str | None = None
    page_token: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PipelineConfig(BaseModel):
    name: str
    sources: list[str]
    sync_mode: SyncMode = SyncMode.INCREMENTAL
    schedule: str | None = None
    max_retries: int = 3
    timeout_seconds: int = 300
    batch_size: int = 100
