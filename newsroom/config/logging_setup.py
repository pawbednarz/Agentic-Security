"""
Structured logging configuration using structlog.

Call `configure_logging()` once at startup (main.py or api/app.py).
All other modules just do: `log = structlog.get_logger(__name__)`
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(level: str = "INFO", fmt: str = "console") -> None:
    """
    Configure structlog + stdlib logging.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR)
        fmt:   'console' for coloured human-readable output,
               'json'    for machine-parseable JSON (production)
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Processors shared by both formats
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if fmt == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(log_level)

    # Quiet noisy libraries
    for noisy in ("httpx", "httpcore", "anthropic", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
