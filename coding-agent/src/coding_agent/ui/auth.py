"""Authentication utilities for HTTP API."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Header, HTTPException, Depends
from fastapi.security import APIKeyHeader

from agentkit.config.loader import load_config as load_agent_toml
from agentkit.errors import ConfigError
from coding_agent.core.config import settings

# API key header scheme
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_SERVER_CONFIG_ENV = "CODING_AGENT_SERVER_CONFIG"


def _server_config_bearer_token() -> str | None:
    configured_path = os.environ.get(_SERVER_CONFIG_ENV)
    if configured_path is None or not configured_path.strip():
        return None
    try:
        server_config = load_agent_toml(
            Path(configured_path).expanduser().resolve()
        ).extra.get("server", {})
    except (ConfigError, OSError):
        return None
    if not isinstance(server_config, dict):
        return None

    bearer_token_env = server_config.get("bearer_token_env")
    if isinstance(bearer_token_env, str) and bearer_token_env.strip():
        token = os.environ.get(bearer_token_env.strip())
        return token.strip() if token is not None and token.strip() else None

    bearer_token = server_config.get("bearer_token")
    if isinstance(bearer_token, str) and bearer_token.strip():
        return bearer_token.strip()
    return None


async def verify_api_key(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None, alias="Authorization"),
) -> str | None:
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
    http_api_key = (
        getattr(settings, "http_api_key", None) or _server_config_bearer_token()
    )
    if not http_api_key:
        return None

    bearer_token: str | None = None
    if authorization is not None:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            bearer_token = value.strip()

    provided_token = x_api_key if isinstance(x_api_key, str) else bearer_token
    if not provided_token:
        raise HTTPException(status_code=401, detail="API key required")

    if provided_token != http_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return provided_token


# Convenience dependency
require_auth = Depends(verify_api_key)
