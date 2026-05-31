"""httpx Auth handler for OAuth Bearer tokens with auto-refresh."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Generator

import httpx

from coding_agent.oauth.types import OAuthTokenSource

logger = logging.getLogger(__name__)


class OAuthBearerAuth(httpx.Auth):
    """httpx Auth that injects an OAuth Bearer token and auto-refreshes on 401.

    This is designed for use with OpenAI-compatible providers that require
    OAuth tokens instead of API keys.
    """

    requires_response_body = True

    def __init__(
        self,
        token_source: OAuthTokenSource,
        *,
        provider_name: str = "codex",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._token_source = token_source
        self._provider_name = provider_name
        self._extra_headers = extra_headers
        self._cached_token: str | None = None

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        """Inject a bearer token for synchronous httpx clients."""
        import asyncio

        snapshot = asyncio.run(self._token_source.get_token())
        self._cached_token = snapshot.access_token
        request.headers["Authorization"] = f"Bearer {snapshot.access_token}"
        if self._extra_headers:
            for key, value in self._extra_headers.items():
                request.headers.setdefault(key, value)
        response = yield request

        if response.status_code == 401:
            logger.debug(
                "OAuth token expired (401), refreshing for %s",
                self._provider_name,
            )
            snapshot = asyncio.run(self._token_source.refresh_token())
            self._cached_token = snapshot.access_token
            request.headers["Authorization"] = f"Bearer {snapshot.access_token}"
            yield request

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        """Inject a bearer token for asynchronous httpx clients."""
        snapshot = await self._token_source.get_token()
        self._cached_token = snapshot.access_token
        request.headers["Authorization"] = f"Bearer {snapshot.access_token}"
        if self._extra_headers:
            for key, value in self._extra_headers.items():
                request.headers.setdefault(key, value)
        response = yield request

        if response.status_code == 401:
            logger.debug(
                "OAuth token expired (401), refreshing for %s",
                self._provider_name,
            )
            snapshot = await self._token_source.refresh_token()
            self._cached_token = snapshot.access_token
            request.headers["Authorization"] = f"Bearer {snapshot.access_token}"
            yield request
