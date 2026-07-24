"""Structured logging + per-request correlation id.

structlog renders event-name + key=value lines to stdout. A ``contextvar`` carries the
correlation id so every log entry within a request is tagged without threading it through
every call. The API middleware sets it at the boundary.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import cast

import structlog
from structlog.typing import EventDict, WrappedLogger

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def set_correlation_id(value: str) -> None:
    _correlation_id.set(value)


def _inject_correlation_id(
    _logger: WrappedLogger, _method: str, event_dict: EventDict
) -> EventDict:
    cid = _correlation_id.get()
    if cid is not None:
        event_dict["correlation_id"] = cid
    return event_dict


def configure_logging(level: int = logging.INFO) -> None:
    """Configure structlog once at startup. Idempotent."""
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        processors=[
            structlog.contextvars.merge_contextvars,
            _inject_correlation_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.KeyValueRenderer(key_order=["timestamp", "level", "event"]),
        ],
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))
