from __future__ import annotations

import os
import sys
from typing import Any

import structlog


_configured = False


def configure_tracing(enabled: bool | None = None, level: str = "INFO") -> None:
    global _configured

    if enabled is None:
        enabled = os.environ.get("AGENTKIT_TRACING", "0") not in (
            "0",
            "",
            "false",
            "False",
        )

    if not enabled:
        structlog.configure(
            processors=[structlog.processors.JSONRenderer()],
            wrapper_class=structlog.stdlib.BoundLogger,
            logger_factory=structlog.PrintLoggerFactory(file=open(os.devnull, "w")),
            cache_logger_on_first_use=False,
        )
        _configured = False
        return

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
    _configured = True


def get_tracer(name: str) -> Any:
    return structlog.get_logger(name)
