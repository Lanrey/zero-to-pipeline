"""Data loading destinations."""

from data_pipeline.loaders.base import BaseLoader
from data_pipeline.loaders.jsonl import JSONLLoader

__all__ = ["BaseLoader", "JSONLLoader"]
