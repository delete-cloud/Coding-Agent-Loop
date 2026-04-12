"""Tests for security features: auth, rate limiting, and input validation."""

from __future__ import annotations

import asyncio
import importlib
import json
from unittest.mock import patch

import pytest
from httpx import AsyncClient, ASGITransport
from httpx_sse import aconnect_sse

from coding_agent.wire.protocol import ApprovalRequest, ToolCallDelta
from coding_agent.ui.http_server import app, session_manager
from coding_agent.ui.rate_limit import limiter
from coding_agent.core.config import settings


@pytest.fixture(autouse=True)
async def clear_sessions():
    """Clear sessions before each test."""
    session_manager.clear_sessions()
    yield
    session_manager.clear_sessions()


@pytest.fixture
async def client():
    """Create async test client."""
    # Reset rate limiter storage before each test
    limiter.reset()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def add_store_backed_approval_request(
    session, session_id: str, request_id: str
) -> None:
    tool_call = ToolCallDelta(
        session_id=session_id,
        tool_name="bash",
        arguments={"command": "ls"},
        call_id=f"call-{request_id}",
    )
    approval_req = ApprovalRequest(
        session_id=session_id,
        request_id=request_id,
        tool_call=tool_call,
        timeout_seconds=120,
    )
    session.approval_store.add_request(approval_req)


@pytest.fixture
async def api_key_client():
    """Create test client with API key auth required."""
    # Reset rate limiter storage before each test
    limiter.reset()
    with patch.object(settings, "http_api_key", "test-secret-key"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


class TestInputValidation:
    """Tests for Pydantic input validation."""

    async def test_create_session_invalid_approval_policy(self, client):
        """Test validation rejects invalid approval policy."""
        response = await client.post(
            "/sessions", json={"approval_policy": "invalid_policy"}
        )
        assert response.status_code == 422
        assert (
            "approval_policy" in str(response.json())
            or "string" in str(response.json()).lower()
        )

    async def test_create_session_valid_policies(self, client):
        """Test validation accepts valid approval policies."""
        for policy in ["yolo", "interactive", "auto"]:
            response = await client.post("/sessions", json={"approval_policy": policy})
            assert response.status_code == 200, f"Policy {policy} should be valid"

    async def test_create_session_repo_path_too_long(self, client):
        """Test validation rejects repo_path exceeding max length."""
        long_path = "/" + "a" * 600
        response = await client.post("/sessions", json={"repo_path": long_path})
        assert response.status_code == 422
        assert "repo_path" in str(response.json()).lower() or "500" in str(
            response.json()
        )

    async def test_send_prompt_empty(self, client):
        """Test validation rejects empty prompt."""
        # Create session first
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        response = await client.post(
            f"/sessions/{session_id}/prompt", json={"prompt": ""}
        )
        assert response.status_code == 422

    async def test_send_prompt_too_long(self, client):
        """Test validation rejects prompt exceeding max length."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        long_prompt = "x" * 10001
        response = await client.post(
            f"/sessions/{session_id}/prompt", json={"prompt": long_prompt}
        )
        assert response.status_code == 422

    async def test_approve_request_id_too_long(self, client):
        """Test validation rejects request_id exceeding max length."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        long_id = "x" * 101
        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": long_id, "approved": True},
        )
        assert response.status_code == 422

    async def test_approve_feedback_too_long(self, client):
        """Test validation rejects feedback exceeding max length."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        long_feedback = "x" * 1001
        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={
                "request_id": "valid-id",
                "approved": True,
                "feedback": long_feedback,
            },
        )
        assert response.status_code == 422

    async def test_approve_missing_request_id(self, client):
        """Test validation rejects missing request_id."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        response = await client.post(
            f"/sessions/{session_id}/approve", json={"approved": True}
        )
        assert response.status_code == 422


class TestApiKeyAuth:
    """Tests for API key authentication."""

    async def test_no_auth_required_when_not_configured(self, client):
        """Test that endpoints work without auth when no key configured."""
        response = await client.post("/sessions")
        assert response.status_code == 200

    async def test_auth_required_when_configured(self, api_key_client):
        """Test that 401 is returned when auth is required but no key provided."""
        response = await api_key_client.post("/sessions")
        assert response.status_code == 401
        assert "api key" in response.json()["detail"].lower()

    async def test_valid_api_key_accepted(self, api_key_client):
        """Test that valid API key is accepted."""
        response = await api_key_client.post(
            "/sessions", headers={"X-API-Key": "test-secret-key"}
        )
        assert response.status_code == 200

    async def test_invalid_api_key_rejected(self, api_key_client):
        """Test that invalid API key is rejected."""
        response = await api_key_client.post(
            "/sessions", headers={"X-API-Key": "wrong-key"}
        )
        assert response.status_code == 401

    async def test_auth_required_for_protected_endpoints(self, api_key_client):
        """Test that auth is required for protected endpoints."""
        # Reset rate limiter
        limiter.reset()

        # Health check works without auth
        response = await api_key_client.get("/healthz")
        assert response.status_code == 200

        response = await api_key_client.get("/readyz")
        assert response.status_code == 200

        # Create session with auth
        response = await api_key_client.post(
            "/sessions", headers={"X-API-Key": "test-secret-key"}
        )
        session_id = response.json()["session_id"]

        # Get session requires auth
        response = await api_key_client.get(f"/sessions/{session_id}")
        assert response.status_code == 401  # No auth header

        response = await api_key_client.get(
            f"/sessions/{session_id}", headers={"X-API-Key": "test-secret-key"}
        )
        assert response.status_code == 200

        # Close session requires auth
        response = await api_key_client.delete(f"/sessions/{session_id}")
        assert response.status_code == 401  # No auth header

        response = await api_key_client.delete(
            f"/sessions/{session_id}", headers={"X-API-Key": "test-secret-key"}
        )
        assert response.status_code == 200


class TestRateLimiting:
    """Tests for rate limiting."""

    async def test_health_endpoint_rate_limited(self, client):
        """Test that health endpoint works with rate limiting enabled."""
        # First request should succeed
        response = await client.get("/healthz")
        assert response.status_code == 200

    def test_rate_limiter_uses_redis_storage_when_env_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        import coding_agent.ui.rate_limit as rate_limit
        import slowapi

        monkeypatch.setenv("AGENT_SESSION_REDIS_URL", "redis://cache.example:6379/0")
        captured: list[str] = []

        original_create = slowapi.Limiter

        def recording_limiter(*args, **kwargs):
            captured.append(kwargs["storage_uri"])
            if kwargs["storage_uri"].startswith("redis://"):
                raise RuntimeError("redis dependency missing")
            return original_create(*args, **kwargs)

        monkeypatch.setattr(slowapi, "Limiter", recording_limiter)
        reloaded = importlib.reload(rate_limit)

        try:
            assert captured == ["redis://cache.example:6379/0", "memory://"]
            assert reloaded.limiter._storage_uri == "memory://"
        finally:
            monkeypatch.delenv("AGENT_SESSION_REDIS_URL", raising=False)
            monkeypatch.setattr(slowapi, "Limiter", original_create)
            importlib.reload(rate_limit)

    async def test_session_creation_rate_limited(self, client):
        """Test that session creation works under normal load."""
        # Make a few requests - should all succeed within rate limit
        for _ in range(5):
            response = await client.post("/sessions")
            assert response.status_code == 200
            await asyncio.sleep(0.05)

    async def test_rate_limit_returns_429(self, client):
        """Test that rate limit returns 429 status code when exceeded."""
        # Reset limiter and set a very low limit for testing
        limiter.reset()

        # Exceed rate limit quickly (10/minute = create 11 sessions rapidly)
        responses = []
        for i in range(12):
            response = await client.post("/sessions")
            responses.append(response.status_code)

        # At least one should be rate limited (429)
        assert 429 in responses, f"Expected at least one 429, got: {responses}"


class TestCorsHeaders:
    """Tests for CORS middleware."""

    async def test_cors_headers_present(self, client):
        """Test that CORS headers are present on responses with Origin header."""
        # CORS headers are added when Origin header is present
        response = await client.get(
            "/healthz",
            headers={"Origin": "http://localhost:3000"},
        )
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers
        # CORS middleware reflects the origin back when allow_origins=["*"]
        assert "localhost:3000" in response.headers["access-control-allow-origin"]

    async def test_cors_preflight_request(self, client):
        """Test CORS preflight request handling."""
        response = await client.options(
            "/sessions",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )
        assert response.status_code in [200, 204]
        assert "access-control-allow-origin" in response.headers
        assert "access-control-allow-methods" in response.headers


class TestResponseSchemas:
    """Tests for response schema validation."""

    async def test_health_response_schema(self, client):
        """Test health response follows schema."""
        response = await client.get("/healthz")
        data = response.json()

        assert "status" in data
        assert "sessions" in data
        assert "version" in data
        assert isinstance(data["sessions"], int)

    async def test_create_session_response_schema(self, client):
        """Test create session response follows schema."""
        # Reset rate limiter to avoid 429
        limiter.reset()

        response = await client.post("/sessions")
        data = response.json()

        assert "session_id" in data
        assert len(data["session_id"]) == 36  # UUID format

    async def test_close_session_response_schema(self, client):
        """Test close session response follows schema."""
        # Reset rate limiter to avoid 429
        limiter.reset()

        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        response = await client.delete(f"/sessions/{session_id}")
        data = response.json()

        assert "status" in data
        assert "session_id" in data
        assert data["status"] == "closed"
        assert data["session_id"] == session_id

    async def test_approve_response_schema(self, client):
        """Test approve response follows schema."""
        # Reset rate limiter to avoid 429
        limiter.reset()

        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        add_store_backed_approval_request(session, session_id, "req123")

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={
                "request_id": "req123",
                "approved": True,
            },
        )
        data = response.json()

        assert "status" in data
        assert "request_id" in data
        assert "decision" in data
        assert data["status"] == "ok"


class TestSecurityIntegration:
    """Integration tests for all security features."""

    async def test_full_flow_with_auth_and_validation(self, api_key_client):
        """Test full session flow with auth and validation."""
        # Reset rate limiter
        limiter.reset()

        headers = {"X-API-Key": "test-secret-key"}

        # Create session
        response = await api_key_client.post(
            "/sessions",
            headers=headers,
            json={"approval_policy": "auto"},
        )
        assert response.status_code == 200
        session_id = response.json()["session_id"]

        # Get session
        response = await api_key_client.get(
            f"/sessions/{session_id}",
            headers=headers,
        )
        assert response.status_code == 200

        # Close session
        response = await api_key_client.delete(
            f"/sessions/{session_id}",
            headers=headers,
        )
        assert response.status_code == 200

    async def test_validation_errors_with_auth(self, api_key_client):
        """Test validation errors work when auth is enabled."""
        # Reset rate limiter
        limiter.reset()

        headers = {"X-API-Key": "test-secret-key"}

        # Invalid approval policy
        response = await api_key_client.post(
            "/sessions",
            headers=headers,
            json={"approval_policy": "invalid"},
        )
        assert response.status_code == 422

        # Missing required field (approval_policy has default, so this is ok)
        response = await api_key_client.post(
            "/sessions",
            headers=headers,
            json={"repo_path": None},  # This is fine (optional)
        )
        assert response.status_code == 200
