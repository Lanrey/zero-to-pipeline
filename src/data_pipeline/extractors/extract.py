"""Extract step implementation for pipeline orchestration."""

from __future__ import annotations

from typing import Any

import structlog

from data_pipeline.auth import AuthManager
from data_pipeline.connectors import ConnectorError, SelfHealingConnector
from data_pipeline.observability.metrics import metrics
from data_pipeline.orchestrator.checkpoint import CheckpointManager
from data_pipeline.schemas import (
    CheckpointState,
    ExtractedRecord,
    SourceConfig,
    SyncMode,
)

logger = structlog.get_logger(__name__)


class ExtractStep:
    """Orchestration-aware extraction step.

    Manages connector lifecycle, checkpoint loading/saving, and metrics.
    Designed to be used as a pipeline step function.
    """

    def __init__(
        self,
        source: SourceConfig,
        resource_type: str,
        auth_manager: AuthManager,
        checkpoint_manager: CheckpointManager,
        *,
        sync_mode: SyncMode = SyncMode.INCREMENTAL,
        batch_size: int = 100,
    ):
        self._source = source
        self._resource_type = resource_type
        self._auth = auth_manager
        self._checkpoints = checkpoint_manager
        self._sync_mode = sync_mode
        self._batch_size = batch_size

    async def __call__(self, *, context: dict[str, Any], prior_results: dict[str, Any]) -> int:
        """Execute extraction. Returns record count."""
        pipeline_id = context["run_id"]
        api = self._source.api
        if api is None:
            raise ConnectorError(f"Source '{self._source.slug}' has no API configuration")
        auth_headers = self._auth.get_auth_header(self._source)
        token = next(iter(auth_headers.values()), "")
        connector = SelfHealingConnector(
            api.base_url,
            credential=token,
            auth_header=api.auth_header,
            auth_prefix=api.auth_prefix,
            default_headers=api.default_headers,
        )

        checkpoint = self._checkpoints.load(pipeline_id, self._source.id)

        records: list[ExtractedRecord] = []
        count = 0

        with metrics.timer(
            "extract_duration", provider=self._source.provider, resource=self._resource_type
        ):
            async for record in connector.extract_with_healing(
                "GET",
                self._resource_type,
                source_id=self._source.id,
                resource_type=self._resource_type,
                checkpoint=checkpoint,
            ):
                records.append(record)
                count += 1

                if len(records) >= self._batch_size:
                    self._flush_batch(records, pipeline_id)
                    records = []

            if records:
                self._flush_batch(records, pipeline_id)

        metrics.increment("records_extracted", count, provider=self._source.provider)
        logger.info(
            "extraction_complete",
            source=self._source.slug,
            resource=self._resource_type,
            records=count,
        )
        return count

    def _flush_batch(self, records: list[ExtractedRecord], pipeline_id: str) -> None:
        if not records:
            return

        last_record = records[-1]
        state = CheckpointState(
            pipeline_id=pipeline_id,
            source_id=self._source.id,
            cursor=last_record.cursor,
            last_record_id=last_record.id,
        )
        self._checkpoints.save(state)
        logger.debug("batch_flushed", count=len(records), cursor=last_record.cursor)
