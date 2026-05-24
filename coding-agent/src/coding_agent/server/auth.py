"""Authentication utilities for HTTP API."""

from __future__ import annotations

import hashlib
import os
import logging
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from fastapi import Header, HTTPException, Depends
from fastapi.security import APIKeyHeader

from agentkit.config.loader import load_config as load_agent_toml
from agentkit.errors import ConfigError
from coding_agent.core.config import settings

# API key header scheme
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_SERVER_CONFIG_ENV = "CODING_AGENT_SERVER_CONFIG"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthContext:
    scope: Literal["user", "admin"]
    # Kept for verify_api_key backward compatibility; exclude it from repr/logging.
    token: str = field(repr=False)
    token_digest: str
    owner_label: str


@dataclass(frozen=True)
class _ConfiguredAuth:
    bearer_token: str | None
    admin_bearer_token: str | None
    token_label_map: dict[str, str]


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _owner_label_for_token(token: str, token_label_map: dict[str, str]) -> str:
    digest = _token_digest(token)
    configured_label = token_label_map.get(digest)
    if configured_label is not None:
        if not configured_label.strip():
            raise RuntimeError("server.token_label_map labels must be non-empty")
        return configured_label.strip()
    return f"owner:{digest}"


def _env_token(server_config: dict[str, object], key: str) -> str | None:
    value = server_config.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    token = os.environ.get(value.strip())
    return token.strip() if token is not None and token.strip() else None


def _direct_token(server_config: dict[str, object], key: str) -> str | None:
    value = server_config.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _token_label_map(server_config: dict[str, object]) -> dict[str, str]:
    raw_map = server_config.get("token_label_map", {})
    if raw_map is None:
        return {}
    if not isinstance(raw_map, dict):
        raise RuntimeError("server.token_label_map must be a mapping")
    labels: dict[str, str] = {}
    for raw_digest, raw_label in cast(dict[object, object], raw_map).items():
        if not isinstance(raw_digest, str) or not raw_digest.strip():
            raise RuntimeError("server.token_label_map keys must be token digests")
        if not isinstance(raw_label, str) or not raw_label.strip():
            raise RuntimeError("server.token_label_map labels must be non-empty")
        labels[raw_digest.strip()] = raw_label.strip()
    return labels


def _server_config_auth() -> _ConfiguredAuth:
    configured_path = os.environ.get(_SERVER_CONFIG_ENV)
    if configured_path is None or not configured_path.strip():
        return _ConfiguredAuth(
            bearer_token=None,
            admin_bearer_token=None,
            token_label_map={},
        )
    try:
        raw_server_config = load_agent_toml(
            Path(configured_path).expanduser().resolve()
        ).extra.get("server", {})
    except (ConfigError, OSError) as exc:
        logger.exception(
            "Failed to load explicit server auth config env=%s path=%s",
            _SERVER_CONFIG_ENV,
            configured_path,
        )
        raise RuntimeError(
            f"failed to load explicit server config: {configured_path}"
        ) from exc
    if not isinstance(raw_server_config, dict):
        return _ConfiguredAuth(
            bearer_token=None,
            admin_bearer_token=None,
            token_label_map={},
        )

    server_config = cast(dict[str, object], raw_server_config)
    return _ConfiguredAuth(
        bearer_token=(
            _env_token(server_config, "bearer_token_env")
            or _direct_token(server_config, "bearer_token")
        ),
        admin_bearer_token=(
            _env_token(server_config, "admin_bearer_token_env")
            or _direct_token(server_config, "admin_bearer_token")
        ),
        token_label_map=_token_label_map(server_config),
    )


def _provided_tokens(
    x_api_key: str | None,
    authorization: str | None,
) -> list[str]:
    bearer_token: str | None = None
    if authorization is not None:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            bearer_token = value.strip()

    provided_tokens: list[str] = []
    if isinstance(x_api_key, str) and x_api_key.strip():
        provided_tokens.append(x_api_key.strip())
    if bearer_token is not None:
        provided_tokens.append(bearer_token)
    return provided_tokens


async def auth_context_from_headers(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None, alias="Authorization"),
) -> AuthContext | None:
    try:
        configured_auth = _server_config_auth()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="Server auth configuration unavailable",
        ) from exc

    http_api_key = getattr(settings, "http_api_key", None)
    user_token = http_api_key or configured_auth.bearer_token
    admin_token = configured_auth.admin_bearer_token
    if not user_token and not admin_token:
        return None

    provided_tokens = _provided_tokens(x_api_key, authorization)
    if not provided_tokens:
        raise HTTPException(status_code=401, detail="API key required")

    for provided_token in provided_tokens:
        if admin_token is not None and secrets.compare_digest(
            provided_token, admin_token
        ):
            return AuthContext(
                scope="admin",
                token=provided_token,
                token_digest=_token_digest(provided_token),
                owner_label=_owner_label_for_token(
                    provided_token,
                    configured_auth.token_label_map,
                ),
            )
        if user_token is not None and secrets.compare_digest(
            provided_token, user_token
        ):
            return AuthContext(
                scope="user",
                token=provided_token,
                token_digest=_token_digest(provided_token),
                owner_label=_owner_label_for_token(
                    provided_token,
                    configured_auth.token_label_map,
                ),
            )

    raise HTTPException(status_code=401, detail="Invalid API key")


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
    context = await auth_context_from_headers(
        x_api_key=x_api_key,
        authorization=authorization,
    )
    return None if context is None else context.token


# Convenience dependency
require_auth = Depends(verify_api_key)
