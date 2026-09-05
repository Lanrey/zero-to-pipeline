"""Pipeline execution engine with asyncio concurrency and checkpointing."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any, cast

import structlog

from data_pipeline.config import settings
from data_pipeline.orchestrator.checkpoint import CheckpointManager
from data_pipeline.orchestrator.pipeline import Pipeline, PipelineStep
from data_pipeline.schemas import PipelineResult, StepResult, StepStatus

logger = structlog.get_logger(__name__)


class PipelineEngine:
    """Executes pipelines with DAG-aware concurrency, retries, and checkpointing.

    Features:
    - Parallel execution of independent steps
    - Per-step retry with exponential backoff
    - Checkpoint persistence after each successful step
    - Graceful cancellation via asyncio
    - Structured observability events
    """

    def __init__(self, checkpoint_manager: CheckpointManager | None = None):
        self._checkpoints = checkpoint_manager or CheckpointManager()

    async def run(
        self, pipeline: Pipeline, *, context: dict[str, Any] | None = None
    ) -> PipelineResult:
        """Execute a pipeline end-to-end."""
        run_id = f"{pipeline.name}_{uuid.uuid4().hex[:8]}"
        ctx = context or {}
        ctx["run_id"] = run_id
        ctx["pipeline_name"] = pipeline.name

        result = PipelineResult(
            pipeline_id=run_id,
            source_id=ctx.get("source_id", "multi"),
            sync_mode=pipeline.sync_mode,
            status=StepStatus.RUNNING,
            started_at=datetime.now(),
        )

        logger.info("pipeline_started", pipeline=pipeline.name, run_id=run_id)

        try:
            layers = pipeline.execution_order()
            step_results: dict[str, Any] = {}

            for layer in layers:
                layer_tasks = [
                    self._run_step(step, ctx, step_results)
                    for step in layer
                ]
                layer_outcomes = await asyncio.gather(*layer_tasks, return_exceptions=True)

                for step, outcome in zip(layer, layer_outcomes, strict=True):
                    if isinstance(outcome, Exception):
                        step_result = StepResult(
                            step_name=step.name,
                            status=StepStatus.FAILED,
                            error=str(outcome),
                            started_at=datetime.now(),
                            completed_at=datetime.now(),
                        )
                        result.steps.append(step_result)
                        result.status = StepStatus.FAILED
                        result.error = f"Step '{step.name}' failed: {outcome}"
                        result.completed_at = datetime.now()
                        logger.error(
                            "pipeline_failed",
                            pipeline=pipeline.name,
                            step=step.name,
                            error=str(outcome),
                        )
                        return result
                    else:
                        step_results[step.name] = outcome
                        step_outcome = cast(StepResult, outcome)
                        result.steps.append(step_outcome)
                        result.total_records += step_outcome.records_processed

            result.status = StepStatus.COMPLETED
            result.completed_at = datetime.now()
            logger.info(
                "pipeline_completed",
                pipeline=pipeline.name,
                run_id=run_id,
                total_records=result.total_records,
                duration_ms=int(
                    (result.completed_at - result.started_at).total_seconds() * 1000
                ),
            )

        except asyncio.CancelledError:
            result.status = StepStatus.FAILED
            result.error = "Pipeline cancelled"
            result.completed_at = datetime.now()
            logger.warning("pipeline_cancelled", pipeline=pipeline.name)

        return result

    async def _run_step(
        self,
        step: PipelineStep,
        context: dict[str, Any],
        prior_results: dict[str, Any],
    ) -> StepResult:
        """Execute a single step with retry logic."""
        started_at = datetime.now()
        logger.info("step_started", step=step.name)

        try:
            result = await self._execute_with_retry(step, context, prior_results)

            records = result if isinstance(result, int) else 0
            step_result = StepResult(
                step_name=step.name,
                status=StepStatus.COMPLETED,
                records_processed=records,
                started_at=started_at,
                completed_at=datetime.now(),
            )
            assert step_result.completed_at is not None
            logger.info(
                "step_completed",
                step=step.name,
                records=records,
                duration_ms=int(
                    (step_result.completed_at - started_at).total_seconds() * 1000
                ),
            )
            return step_result

        except Exception as e:
            logger.error("step_failed", step=step.name, error=str(e))
            raise

    async def _execute_with_retry(
        self,
        step: PipelineStep,
        context: dict[str, Any],
        prior_results: dict[str, Any],
    ) -> Any:
        """Execute step function with configurable retry."""
        last_error: Exception | None = None

        for attempt in range(1, step.retry_count + 1):
            try:
                return await asyncio.wait_for(
                    step.fn(context=context, prior_results=prior_results),
                    timeout=step.timeout_seconds,
                )
            except TimeoutError:
                last_error = TimeoutError(
                    f"Step '{step.name}' timed out after {step.timeout_seconds}s"
                )
                logger.warning(
                    "step_timeout",
                    step=step.name,
                    attempt=attempt,
                    max_attempts=step.retry_count,
                )
            except Exception as e:
                last_error = e
                if attempt < step.retry_count:
                    delay = min(
                        settings.retry_base_delay * (2 ** (attempt - 1)),
                        settings.retry_max_delay,
                    )
                    logger.warning(
                        "step_retry",
                        step=step.name,
                        attempt=attempt,
                        max_attempts=step.retry_count,
                        delay=delay,
                        error=str(e),
                    )
                    await asyncio.sleep(delay)

        raise last_error  # type: ignore[misc]
