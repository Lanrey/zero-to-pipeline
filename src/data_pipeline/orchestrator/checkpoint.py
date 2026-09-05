"""Checkpoint management for resumable, idempotent pipeline execution."""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from data_pipeline.config import settings
from data_pipeline.schemas import CheckpointState

logger = structlog.get_logger(__name__)


class CheckpointManager:
    """Persists pipeline state for crash recovery and incremental syncs.

    Each source+resource combination gets its own checkpoint file.
    On restart, the pipeline resumes from the last successful checkpoint.
    """

    def __init__(self, checkpoint_dir: Path | None = None):
        self._dir = checkpoint_dir or settings.checkpoint_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _checkpoint_path(self, pipeline_id: str, source_id: str) -> Path:
        return self._dir / f"{pipeline_id}__{source_id}.json"

    def save(self, state: CheckpointState) -> None:
        path = self._checkpoint_path(state.pipeline_id, state.source_id)
        data = state.model_dump(mode="json")
        path.write_text(json.dumps(data, indent=2, default=str))
        logger.debug(
            "checkpoint_saved",
            pipeline_id=state.pipeline_id,
            source_id=state.source_id,
            cursor=state.cursor,
        )

    def load(self, pipeline_id: str, source_id: str) -> CheckpointState | None:
        path = self._checkpoint_path(pipeline_id, source_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return CheckpointState(**data)
        except Exception as e:
            logger.warning("checkpoint_load_failed", error=str(e), path=str(path))
            return None

    def clear(self, pipeline_id: str, source_id: str) -> None:
        path = self._checkpoint_path(pipeline_id, source_id)
        if path.exists():
            path.unlink()
            logger.info("checkpoint_cleared", pipeline_id=pipeline_id, source_id=source_id)

    def clear_all(self, pipeline_id: str) -> int:
        """Remove all checkpoints for a pipeline. Returns count of removed files."""
        count = 0
        prefix = f"{pipeline_id}__"
        for path in self._dir.iterdir():
            if path.name.startswith(prefix) and path.suffix == ".json":
                path.unlink()
                count += 1
        if count:
            logger.info("checkpoints_cleared", pipeline_id=pipeline_id, count=count)
        return count
