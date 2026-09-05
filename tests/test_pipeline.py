"""Tests for pipeline orchestration engine."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from data_pipeline.orchestrator import CheckpointManager, Pipeline, PipelineEngine
from data_pipeline.schemas import StepStatus, SyncMode


@pytest.fixture
def tmp_checkpoint_dir(tmp_path):
    return tmp_path / "checkpoints"


@pytest.fixture
def checkpoint_manager(tmp_checkpoint_dir):
    return CheckpointManager(checkpoint_dir=tmp_checkpoint_dir)


@pytest.fixture
def engine(checkpoint_manager):
    return PipelineEngine(checkpoint_manager=checkpoint_manager)


class TestPipeline:
    def test_add_step(self):
        pipeline = Pipeline("test")

        async def step_fn(*, context, prior_results):
            return 10

        pipeline.add_step("extract", step_fn)
        assert len(pipeline.steps) == 1
        assert pipeline.steps[0].name == "extract"

    def test_execution_order_linear(self):
        pipeline = Pipeline("test")

        async def noop(*, context, prior_results):
            return 0

        pipeline.add_step("a", noop)
        pipeline.add_step("b", noop, depends_on=["a"])
        pipeline.add_step("c", noop, depends_on=["b"])

        layers = pipeline.execution_order()
        assert len(layers) == 3
        assert layers[0][0].name == "a"
        assert layers[1][0].name == "b"
        assert layers[2][0].name == "c"

    def test_execution_order_parallel(self):
        pipeline = Pipeline("test")

        async def noop(*, context, prior_results):
            return 0

        pipeline.add_step("a", noop)
        pipeline.add_step("b", noop)
        pipeline.add_step("c", noop, depends_on=["a", "b"])

        layers = pipeline.execution_order()
        assert len(layers) == 2
        assert len(layers[0]) == 2  # a and b run in parallel
        assert layers[1][0].name == "c"

    def test_circular_dependency_detected(self):
        pipeline = Pipeline("test")

        async def noop(*, context, prior_results):
            return 0

        pipeline.add_step("a", noop)
        pipeline.add_step("b", noop, depends_on=["a"])
        # Manually create circular dependency
        pipeline._steps["a"].depends_on = ["b"]

        with pytest.raises(ValueError, match="Circular dependency"):
            pipeline.execution_order()

    def test_undefined_dependency_rejected(self):
        pipeline = Pipeline("test")

        async def noop(*, context, prior_results):
            return 0

        with pytest.raises(ValueError, match="undefined steps"):
            pipeline.add_step("b", noop, depends_on=["nonexistent"])


class TestPipelineEngine:
    @pytest.mark.asyncio
    async def test_run_simple_pipeline(self, engine):
        pipeline = Pipeline("test")
        call_order = []

        async def step_a(*, context, prior_results):
            call_order.append("a")
            return 5

        async def step_b(*, context, prior_results):
            call_order.append("b")
            return 3

        pipeline.add_step("a", step_a)
        pipeline.add_step("b", step_b, depends_on=["a"])

        result = await engine.run(pipeline)

        assert result.status == StepStatus.COMPLETED
        assert call_order == ["a", "b"]
        assert result.total_records == 8

    @pytest.mark.asyncio
    async def test_parallel_execution(self, engine):
        pipeline = Pipeline("test")
        timestamps = {}

        async def step_a(*, context, prior_results):
            timestamps["a_start"] = asyncio.get_event_loop().time()
            await asyncio.sleep(0.1)
            timestamps["a_end"] = asyncio.get_event_loop().time()
            return 1

        async def step_b(*, context, prior_results):
            timestamps["b_start"] = asyncio.get_event_loop().time()
            await asyncio.sleep(0.1)
            timestamps["b_end"] = asyncio.get_event_loop().time()
            return 1

        pipeline.add_step("a", step_a)
        pipeline.add_step("b", step_b)

        result = await engine.run(pipeline)

        assert result.status == StepStatus.COMPLETED
        # Both started at roughly the same time (parallel)
        assert abs(timestamps["a_start"] - timestamps["b_start"]) < 0.05

    @pytest.mark.asyncio
    async def test_step_failure_stops_pipeline(self, engine):
        pipeline = Pipeline("test")

        async def failing_step(*, context, prior_results):
            raise RuntimeError("Simulated failure")

        async def never_reached(*, context, prior_results):
            return 0

        pipeline.add_step("fail", failing_step)
        pipeline.add_step("after", never_reached, depends_on=["fail"])

        result = await engine.run(pipeline)

        assert result.status == StepStatus.FAILED
        assert "Simulated failure" in result.error

    @pytest.mark.asyncio
    async def test_retry_on_transient_failure(self, engine):
        pipeline = Pipeline("test")
        attempts = []

        async def flaky_step(*, context, prior_results):
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("Transient error")
            return 1

        pipeline.add_step("flaky", flaky_step, retry_count=3)

        result = await engine.run(pipeline)

        assert result.status == StepStatus.COMPLETED
        assert len(attempts) == 3


class TestCheckpointManager:
    def test_save_and_load(self, checkpoint_manager):
        from data_pipeline.schemas import CheckpointState

        state = CheckpointState(
            pipeline_id="test-pipeline",
            source_id="linear-123",
            cursor="abc123",
            last_record_id="rec-456",
        )
        checkpoint_manager.save(state)
        loaded = checkpoint_manager.load("test-pipeline", "linear-123")

        assert loaded is not None
        assert loaded.cursor == "abc123"
        assert loaded.last_record_id == "rec-456"

    def test_load_nonexistent(self, checkpoint_manager):
        result = checkpoint_manager.load("nonexistent", "source")
        assert result is None

    def test_clear(self, checkpoint_manager):
        from data_pipeline.schemas import CheckpointState

        state = CheckpointState(
            pipeline_id="test", source_id="src", cursor="x"
        )
        checkpoint_manager.save(state)
        checkpoint_manager.clear("test", "src")
        assert checkpoint_manager.load("test", "src") is None

    def test_clear_all(self, checkpoint_manager):
        from data_pipeline.schemas import CheckpointState

        for i in range(3):
            state = CheckpointState(
                pipeline_id="batch", source_id=f"src-{i}", cursor=f"c{i}"
            )
            checkpoint_manager.save(state)

        count = checkpoint_manager.clear_all("batch")
        assert count == 3
