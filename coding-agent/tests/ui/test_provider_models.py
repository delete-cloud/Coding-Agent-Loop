"""Tests for the GET /providers/{provider}/models endpoint and its helper."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

import coding_agent.server.http_server as http_server
from coding_agent.core.config import settings
from coding_agent.plugins.llm_provider import LLMProviderPlugin
from coding_agent.providers.openai_compat import OpenAICompatProvider
from coding_agent.server.http_server import app
from coding_agent.server.provider_models import list_provider_models
from coding_agent.server.rate_limit import limiter


@pytest.fixture
async def client():
    """Create async test client with a fresh rate limiter."""
    limiter.reset()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestProviderModelsEndpoint:
    """Endpoint-level tests with the provider client mocked out."""

    async def test_live_listing_returns_model_ids(self, client, monkeypatch):
        async def fake_list_provider_models(provider, *, timeout=10.0):
            assert provider == "kimi-code"
            return ["kimi-for-coding", "kimi-for-coding-highspeed", "k3", "k3-256k"]

        monkeypatch.setattr(
            http_server, "list_provider_models", fake_list_provider_models
        )

        response = await client.get("/providers/kimi-code/models")

        assert response.status_code == 200
        assert response.json() == {
            "provider": "kimi-code",
            "models": [
                {"id": "kimi-for-coding"},
                {"id": "kimi-for-coding-highspeed"},
                {"id": "k3"},
                {"id": "k3-256k"},
            ],
            "source": "live",
        }

    async def test_unknown_provider_returns_422(self, client):
        response = await client.get("/providers/not-a-provider/models")

        assert response.status_code == 422

    async def test_listing_failure_returns_unavailable(self, client, monkeypatch):
        async def failing_list_provider_models(provider, *, timeout=10.0):
            raise RuntimeError("no API key configured")

        monkeypatch.setattr(
            http_server, "list_provider_models", failing_list_provider_models
        )

        response = await client.get("/providers/deepseek/models")

        assert response.status_code == 200
        assert response.json() == {
            "provider": "deepseek",
            "models": [],
            "source": "unavailable",
        }

    async def test_empty_listing_returns_unavailable(self, client, monkeypatch):
        async def empty_list_provider_models(provider, *, timeout=10.0):
            return []

        monkeypatch.setattr(
            http_server, "list_provider_models", empty_list_provider_models
        )

        response = await client.get("/providers/anthropic/models")

        assert response.status_code == 200
        assert response.json() == {
            "provider": "anthropic",
            "models": [],
            "source": "unavailable",
        }

    async def test_requires_api_key_when_configured(self, monkeypatch):
        async def fake_list_provider_models(provider, *, timeout=10.0):
            return ["k3"]

        monkeypatch.setattr(
            http_server, "list_provider_models", fake_list_provider_models
        )
        limiter.reset()
        with patch.object(settings, "http_api_key", "test-secret-key"):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as authed_client:
                unauthenticated = await authed_client.get("/providers/kimi-code/models")
                authenticated = await authed_client.get(
                    "/providers/kimi-code/models",
                    headers={"X-API-Key": "test-secret-key"},
                )

        assert unauthenticated.status_code == 401
        assert authenticated.status_code == 200
        assert authenticated.json()["source"] == "live"


class TestListProviderModelsHelper:
    """Helper-level tests: provider reuse, filtering, timeout, cleanup."""

    async def test_openai_compatible_provider_returns_ids_and_closes_client(
        self, monkeypatch
    ):
        captured: dict[str, OpenAICompatProvider] = {}

        real_init = OpenAICompatProvider.__init__

        def tracking_init(self, *args, **kwargs):
            real_init(self, *args, **kwargs)
            captured["instance"] = self

        async def fake_list_models(self):
            return ["kimi-for-coding", "k3"]

        monkeypatch.setattr(OpenAICompatProvider, "__init__", tracking_init)
        monkeypatch.setattr(OpenAICompatProvider, "list_models", fake_list_models)

        ids = await list_provider_models("kimi-code")

        assert ids == ["kimi-for-coding", "k3"]
        # The short-lived dedicated client is closed after the call.
        assert captured["instance"]._http_client is None

    async def test_non_openai_compatible_provider_returns_empty(self, monkeypatch):
        sentinel = object()

        monkeypatch.setattr(
            LLMProviderPlugin, "provide_llm", lambda self, **kwargs: sentinel
        )

        assert await list_provider_models("anthropic") == []

    async def test_listing_hits_timeout(self, monkeypatch):
        async def slow_list_models(self):
            await asyncio.sleep(5)
            return ["never"]

        monkeypatch.setattr(OpenAICompatProvider, "list_models", slow_list_models)

        with pytest.raises(asyncio.TimeoutError):
            await list_provider_models("openai", timeout=0.05)


class TestCodexProviderModels:
    """Codex live listing through the endpoint and the helper."""

    async def test_codex_live_listing_success(self, client, monkeypatch):
        async def fake_list_provider_models(provider, *, timeout=10.0):
            assert provider == "codex"
            return ["gpt-5.6-sol", "gpt-5.5", "gpt-5.4"]

        monkeypatch.setattr(
            http_server, "list_provider_models", fake_list_provider_models
        )

        response = await client.get("/providers/codex/models")

        assert response.status_code == 200
        assert response.json() == {
            "provider": "codex",
            "models": [
                {"id": "gpt-5.6-sol"},
                {"id": "gpt-5.5"},
                {"id": "gpt-5.4"},
            ],
            "source": "live",
        }

    async def test_codex_label_key_routes_to_listing(self, client, monkeypatch):
        async def fake_list_provider_models(provider, *, timeout=10.0):
            assert provider == "codex:work"
            return ["gpt-5.5"]

        monkeypatch.setattr(
            http_server, "list_provider_models", fake_list_provider_models
        )

        response = await client.get("/providers/codex:work/models")

        assert response.status_code == 200
        assert response.json() == {
            "provider": "codex:work",
            "models": [{"id": "gpt-5.5"}],
            "source": "live",
        }

    async def test_codex_listing_failure_returns_unavailable(self, client, monkeypatch):
        async def failing_list_provider_models(provider, *, timeout=10.0):
            raise RuntimeError("codex account not connected: codex")

        monkeypatch.setattr(
            http_server, "list_provider_models", failing_list_provider_models
        )

        response = await client.get("/providers/codex/models")

        assert response.status_code == 200
        assert response.json() == {
            "provider": "codex",
            "models": [],
            "source": "unavailable",
        }

    async def test_invalid_codex_label_returns_422(self, client):
        response = await client.get("/providers/codex:Bad_Label!/models")

        assert response.status_code == 422


class TestListProviderModelsCodexHelper:
    """Helper-level codex tests: plugin routing and client cleanup."""

    async def test_codex_provider_returns_ids_and_closes_client(self, monkeypatch):
        from coding_agent.oauth.types import OAuthAccount, TokenSnapshot
        from coding_agent.providers.codex_responses import CodexResponsesProvider

        class _TokenSource:
            async def get_token(self):
                return TokenSnapshot(
                    provider_name="codex:work",
                    access_token="token",
                    account=OAuthAccount(chatgpt_account_id="account-123"),
                )

            async def refresh_token(self):
                raise AssertionError("refresh should not be needed")

        def handler(request):
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"slug": "gpt-5.5", "visibility": "list", "priority": 2},
                        {"slug": "gpt-5.6-sol", "visibility": "list", "priority": 1},
                    ]
                },
                request=request,
            )

        instance = CodexResponsesProvider(
            model="",
            token_source=_TokenSource(),
            base_url="https://chatgpt.com/backend-api/codex",
            transport=httpx.MockTransport(handler),
        )
        captured: dict[str, object] = {}

        def fake_provide_llm(self, **kwargs):
            captured["provider"] = self._provider_name
            return instance

        monkeypatch.setattr(LLMProviderPlugin, "provide_llm", fake_provide_llm)

        ids = await list_provider_models("codex:work")

        assert captured["provider"] == "codex:work"
        assert ids == ["gpt-5.6-sol", "gpt-5.5"]
        # The short-lived client is closed after the call.
        assert instance._http_client.is_closed
