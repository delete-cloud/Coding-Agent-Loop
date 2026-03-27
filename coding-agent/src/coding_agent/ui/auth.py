"""Authentication utilities for HTTP API."""

from __future__ import annotations

from fastapi import Header, HTTPException, Depends
from fastapi.security import APIKeyHeader

from coding_agent.core.config import settings

# API key header scheme
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(x_api_key: str | None = Header(None, alias="X-API-Key")) -> str | None:
    """Verify API key from header.
    
    If no API key is configured in settings, authentication is disabled.
    If API key is configured, the request must provide a matching key.
    
    Args:
        x_api_key: The API key from the X-API-Key header.
        
    Returns:
        The API key if valid, or None if auth is disabled.
        
    Raises:
        HTTPException: 401 if the API key is invalid.
    """
    # No auth required if no key configured
    http_api_key = getattr(settings, 'http_api_key', None)
    if not http_api_key:
        return None
    
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key required")
    
    if x_api_key != http_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    return x_api_key


# Convenience dependency
require_auth = Depends(verify_api_key)
