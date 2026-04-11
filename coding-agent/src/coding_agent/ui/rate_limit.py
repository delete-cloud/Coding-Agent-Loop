"""Rate limiting configuration for HTTP API."""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address


# Create global limiter instance
# Uses client IP address as the rate limit key
# Note: In production, use Redis storage for distributed rate limiting
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100/minute"],
    storage_uri="memory://",  # Use in-memory storage (reset on restart)
)


# Common rate limits
class RateLimits:
    """Predefined rate limits for different endpoint types."""
    
    # Strict limits for resource-intensive operations
    CREATE_SESSION = "10/minute"
    SEND_PROMPT = "20/minute"
    
    # Moderate limits for regular operations
    APPROVE = "30/minute"
    GET_SESSION = "60/minute"
    CLOSE_SESSION = "20/minute"
    
    # Lenient limits for health checks and streaming
    HEALTH = "100/minute"
    EVENTS = "30/minute"
