"""Lightweight metrics collection for pipeline observability."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class MetricPoint:
    name: str
    value: float
    tags: dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class MetricsCollector:
    """In-process metrics collector with structured log emission.

    Collects counters, gauges, and histograms. Emits to structured logs
    for downstream aggregation (Prometheus, Datadog, etc.).
    """

    def __init__(self) -> None:
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = defaultdict(list)

    def increment(self, name: str, value: float = 1.0, **tags: str) -> None:
        key = self._key(name, tags)
        self._counters[key] += value

    def gauge(self, name: str, value: float, **tags: str) -> None:
        key = self._key(name, tags)
        self._gauges[key] = value

    def histogram(self, name: str, value: float, **tags: str) -> None:
        key = self._key(name, tags)
        self._histograms[key].append(value)

    @contextmanager
    def timer(self, name: str, **tags: str) -> Generator[None, None, None]:
        """Context manager that records duration as a histogram value."""
        start = time.perf_counter()
        try:
            yield
        finally:
            duration = time.perf_counter() - start
            self.histogram(name, duration, **tags)
            logger.debug(
                "metric_timer",
                metric=name,
                duration_ms=round(duration * 1000, 2),
                **tags,
            )

    def emit_all(self) -> list[MetricPoint]:
        """Emit all collected metrics as structured log events."""
        points: list[MetricPoint] = []

        for key, value in self._counters.items():
            name, tags = self._parse_key(key)
            point = MetricPoint(name=name, value=value, tags=tags)
            points.append(point)
            logger.info("metric_counter", metric=name, value=value, **tags)

        for key, value in self._gauges.items():
            name, tags = self._parse_key(key)
            point = MetricPoint(name=name, value=value, tags=tags)
            points.append(point)
            logger.info("metric_gauge", metric=name, value=value, **tags)

        for key, values in self._histograms.items():
            name, tags = self._parse_key(key)
            if values:
                point = MetricPoint(
                    name=name,
                    value=sum(values) / len(values),
                    tags={**tags, "aggregation": "mean"},
                )
                points.append(point)
                logger.info(
                    "metric_histogram",
                    metric=name,
                    count=len(values),
                    mean=round(sum(values) / len(values), 4),
                    p99=round(
                        sorted(values)[int(len(values) * 0.99)], 4
                    ) if len(values) > 1 else round(values[0], 4),
                    **tags,
                )

        return points

    def reset(self) -> None:
        self._counters.clear()
        self._gauges.clear()
        self._histograms.clear()

    @staticmethod
    def _key(name: str, tags: dict[str, str]) -> str:
        if not tags:
            return name
        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}|{tag_str}"

    @staticmethod
    def _parse_key(key: str) -> tuple[str, dict[str, str]]:
        if "|" not in key:
            return key, {}
        name, tag_str = key.split("|", 1)
        tags = dict(pair.split("=", 1) for pair in tag_str.split(","))
        return name, tags


metrics = MetricsCollector()
