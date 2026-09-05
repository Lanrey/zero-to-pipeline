"""Persistent source store — saves source configs to the workspace directory."""

from __future__ import annotations

import contextlib
import json
from datetime import datetime
from pathlib import Path

import structlog

from data_pipeline.config import settings
from data_pipeline.schemas import ConnectionStatus, SourceConfig

logger = structlog.get_logger(__name__)


class SourceStore:
    """Persists added sources as JSON files under the workspace directory.

    Layout:
        <workspace_dir>/sources/<slug>/config.json

    Supports: list, get, save, delete, update connection status.
    """

    def __init__(self, base_dir: Path | None = None):
        self._base_dir = base_dir or (settings.workspace_dir / "sources")
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _source_dir(self, slug: str) -> Path:
        return self._base_dir / slug

    def _config_path(self, slug: str) -> Path:
        return self._source_dir(slug) / "config.json"

    def save(self, source: SourceConfig) -> None:
        """Save or update a source config to disk."""
        source.updated_at = datetime.now()
        source_dir = self._source_dir(source.slug)
        source_dir.mkdir(parents=True, exist_ok=True)
        path = self._config_path(source.slug)
        data = source.model_dump(mode="json")
        path.write_text(json.dumps(data, indent=2, default=str))
        logger.info("source_saved", slug=source.slug, path=str(path))

    def get(self, slug: str) -> SourceConfig | None:
        """Load a source config by slug. Returns None if not found."""
        path = self._config_path(slug)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return SourceConfig(**data)
        except Exception as e:
            logger.warning("source_load_failed", slug=slug, error=str(e))
            return None

    def list(self) -> list[SourceConfig]:
        """List all persisted sources."""
        sources: list[SourceConfig] = []
        if not self._base_dir.exists():
            return sources
        for source_dir in sorted(self._base_dir.iterdir()):
            if source_dir.is_dir():
                config_path = source_dir / "config.json"
                if config_path.exists():
                    try:
                        data = json.loads(config_path.read_text())
                        sources.append(SourceConfig(**data))
                    except Exception as e:
                        logger.warning(
                            "source_list_skip",
                            dir=str(source_dir),
                            error=str(e),
                        )
        return sources

    def delete(self, slug: str) -> bool:
        """Remove a source config. Returns True if deleted, False if not found."""
        path = self._config_path(slug)
        if not path.exists():
            return False
        path.unlink()
        source_dir = self._source_dir(slug)
        with contextlib.suppress(OSError):
            source_dir.rmdir()
        logger.info("source_deleted", slug=slug)
        return True

    def update_status(
        self,
        slug: str,
        status: ConnectionStatus,
        last_sync_at: datetime | None = None,
    ) -> bool:
        """Update connection status (and optionally last_sync_at) for a source."""
        source = self.get(slug)
        if source is None:
            return False
        source.connection_status = status
        if last_sync_at is not None:
            source.last_sync_at = last_sync_at
        self.save(source)
        return True

    def exists(self, slug: str) -> bool:
        """Check if a source config exists."""
        return self._config_path(slug).exists()
