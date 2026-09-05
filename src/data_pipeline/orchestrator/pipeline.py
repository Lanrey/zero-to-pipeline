"""Pipeline definition and step composition."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

import structlog

from data_pipeline.schemas import SyncMode

logger = structlog.get_logger(__name__)

StepFn = Callable[..., Coroutine[Any, Any, Any]]


@dataclass
class PipelineStep:
    """A single step in a pipeline DAG."""

    name: str
    fn: StepFn
    depends_on: list[str] = field(default_factory=list)
    retry_count: int = 3
    timeout_seconds: int = 300


class Pipeline:
    """Declarative pipeline builder.

    Usage:
        pipeline = Pipeline("my-pipeline")
        pipeline.add_step("extract", extract_fn)
        pipeline.add_step("transform", transform_fn, depends_on=["extract"])
        pipeline.add_step("load", load_fn, depends_on=["transform"])
    """

    def __init__(
        self,
        name: str,
        *,
        sync_mode: SyncMode = SyncMode.INCREMENTAL,
        max_retries: int = 3,
    ):
        self.name = name
        self.sync_mode = sync_mode
        self.max_retries = max_retries
        self._steps: dict[str, PipelineStep] = {}

    def add_step(
        self,
        name: str,
        fn: StepFn,
        *,
        depends_on: list[str] | None = None,
        retry_count: int | None = None,
        timeout_seconds: int = 300,
    ) -> Pipeline:
        if depends_on:
            missing = [d for d in depends_on if d not in self._steps]
            if missing:
                raise ValueError(
                    f"Step '{name}' depends on undefined steps: {missing}"
                )

        self._steps[name] = PipelineStep(
            name=name,
            fn=fn,
            depends_on=depends_on or [],
            retry_count=retry_count if retry_count is not None else self.max_retries,
            timeout_seconds=timeout_seconds,
        )
        return self

    @property
    def steps(self) -> list[PipelineStep]:
        return list(self._steps.values())

    def execution_order(self) -> list[list[PipelineStep]]:
        """Compute execution layers (steps that can run in parallel)."""
        remaining = dict(self._steps)
        completed: set[str] = set()
        layers: list[list[PipelineStep]] = []

        while remaining:
            layer = [
                step for name, step in remaining.items()
                if all(dep in completed for dep in step.depends_on)
            ]
            if not layer:
                unresolved = list(remaining.keys())
                raise ValueError(f"Circular dependency detected among: {unresolved}")

            layers.append(layer)
            for step in layer:
                completed.add(step.name)
                del remaining[step.name]

        return layers
