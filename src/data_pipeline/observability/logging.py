"""Structured logging configuration with structlog."""

from __future__ import annotations

import logging
import sys

import structlog

from data_pipeline.config import settings


def configure_logging(level: str | None = None, log_format: str | None = None) -> None:
    """Configure structured logging for the pipeline."""
    log_level = getattr(logging, (level or settings.log_level).upper(), logging.INFO)
    effective_format = log_format or settings.log_format

    if effective_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.filter_by_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.UnicodeDecoder(),
            renderer,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=log_level,
    )
