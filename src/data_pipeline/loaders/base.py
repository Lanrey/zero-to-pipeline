"""Base loader interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from data_pipeline.schemas import NormalizedRecord


class BaseLoader(ABC):
    """Base class for data loaders (destinations)."""

    @abstractmethod
    async def load(self, records: list[NormalizedRecord]) -> int:
        """Load records to destination. Returns count of records loaded."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Clean up resources."""
        ...
