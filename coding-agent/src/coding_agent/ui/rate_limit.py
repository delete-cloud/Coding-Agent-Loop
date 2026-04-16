"""Rate limiting configuration for HTTP API."""

from __future__ import annotations

import logging
import os
from typing import Final

from slowapi import Limiter
from slowapi.util import get_remote_address


logger = logging.getLogger(__name__)


def _get_storage_uri() -> str:
    redis_url = os.environ.get("AGENT_SESSION_REDIS_URL", "")
    if redis_url:
        return redis_url
    return "memory://"


def _create_limiter() -> Limiter:
    storage_uri = _get_storage_uri()
    try:
        return Limiter(
            key_func=get_remote_address,
            default_limits=["100/minute"],
            storage_uri=storage_uri,
        )
    except Exception as exc:
        logger.warning(
            "Rate limiter storage unavailable at %s; falling back to memory: %s",
            storage_uri,
            exc,
        )
        return Limiter(
            key_func=get_remote_address,
            default_limits=["100/minute"],
            storage_uri="memory://",
        )


limiter = _create_limiter()


# Common rate limits
class RateLimits:
    """Predefined rate limits for different endpoint types."""

    # Strict limits for resource-intensive operations
    CREATE_SESSION: Final[str] = "10/minute"
    SEND_PROMPT: Final[str] = "20/minute"

    # Moderate limits for regular operations
    APPROVE: Final[str] = "30/minute"
    GET_SESSION: Final[str] = "60/minute"
    CLOSE_SESSION: Final[str] = "20/minute"
    LIST_CHECKPOINTS: Final[str] = "60/minute"
    RESTORE_CHECKPOINT: Final[str] = "20/minute"

    # Lenient limits for health checks and streaming
    HEALTH: Final[str] = "100/minute"
    EVENTS: Final[str] = "30/minute"
