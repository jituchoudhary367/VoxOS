"""
VoxOS Structured Logger.

Uses structlog for JSON-formatted, correlated log output.
Every log entry automatically includes timestamp, level, and caller info.

Usage:
    from voxos.observability.logger import get_logger
    log = get_logger(__name__)
    log.info("task_started", task_id=str(task_id), agent="executor")
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from voxos.config import get_settings


def _add_service_info(logger: Any, method: str, event_dict: EventDict) -> EventDict:
    """Add service name and environment to every log entry."""
    event_dict["service"] = "voxos"
    event_dict["env"] = get_settings().env
    return event_dict


def _redact_secrets(logger: Any, method: str, event_dict: EventDict) -> EventDict:
    """Redact sensitive values from log output."""
    SENSITIVE_KEYS = {
        "password", "api_key", "secret", "token", "authorization",
        "cookie", "credential", "private_key", "ssh_key",
    }
    for key in list(event_dict.keys()):
        if any(s in key.lower() for s in SENSITIVE_KEYS):
            event_dict[key] = "***REDACTED***"
    # Saved-login values, wherever they appear. Only secrets the vault has
    # already loaded are known here, so logging never touches the keyring.
    vault_module = sys.modules.get("voxos.vault.store")
    vault = getattr(vault_module, "_vault", None) if vault_module else None
    known = getattr(vault, "_seen", None)
    if known:
        for key, value in event_dict.items():
            if isinstance(value, str) and any(len(s) >= 4 and s in value for s in known):
                event_dict[key] = vault.redact(value)
    return event_dict


def configure_logging() -> None:
    """Configure structlog and stdlib logging. Call once at startup."""
    settings = get_settings()
    log_level = getattr(logging, settings.observability.log_level.upper(), logging.INFO)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        _add_service_info,
        _redact_secrets,
    ]

    if settings.observability.log_format == "json":
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(log_level)

    # Quiet noisy third-party loggers
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "asyncpg"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a named structlog logger."""
    return structlog.get_logger(name)
