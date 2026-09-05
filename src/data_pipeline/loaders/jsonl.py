"""JSONL file loader for local development and data lake patterns."""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from data_pipeline.loaders.base import BaseLoader
from data_pipeline.schemas import NormalizedRecord

logger = structlog.get_logger(__name__)


class JSONLLoader(BaseLoader):
    """Load normalized records to JSONL files.

    Writes one file per source+resource combination.
    Suitable for local development, data lake ingestion, and batch processing.
    """

    def __init__(self, output_dir: Path):
        self._output_dir = output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)

    async def load(self, records: list[NormalizedRecord]) -> int:
        if not records:
            return 0

        grouped: dict[str, list[NormalizedRecord]] = {}
        for record in records:
            key = f"{record.source_id}__{record.resource_type}"
            grouped.setdefault(key, []).append(record)

        total = 0
        for key, group in grouped.items():
            output_path = self._output_dir / f"{key}.jsonl"
            with output_path.open("a", encoding="utf-8") as f:
                for record in group:
                    f.write(json.dumps(record.model_dump(mode="json"), default=str) + "\n")
                    total += 1

            logger.debug("records_written", file=str(output_path), count=len(group))

        return total

    async def close(self) -> None:
        pass
