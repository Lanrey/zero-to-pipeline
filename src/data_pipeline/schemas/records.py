"""Data record models for extraction and normalization."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ExtractedRecord(BaseModel):
    id: str
    source_id: str
    resource_type: str
    raw_data: dict[str, Any]
    extracted_at: datetime = Field(default_factory=datetime.now)
    cursor: str | None = None


class NormalizedRecord(BaseModel):
    id: str
    source_id: str
    resource_type: str
    data: dict[str, Any]
    schema_version: str = "1.0"
    normalized_at: datetime = Field(default_factory=datetime.now)
    metadata: dict[str, Any] = Field(default_factory=dict)
