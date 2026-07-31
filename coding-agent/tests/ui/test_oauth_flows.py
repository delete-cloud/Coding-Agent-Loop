"""Tests for codex OAuth device-flow endpoints (multi-account).

All network access is mocked via a fake ``CodexOAuthClient``; the OAuth store
is file-backed under ``tmp_path``.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

import coding_agent.server.http_server as http_server
from coding_agent.oauth.codex import DeviceCode
from coding_agent.oauth.store import OAuthStore
from coding_agent.oauth.types import OAuthAccount, OAuthProviderRecord, OAuthTokens
from coding_agent.server.oauth_flows import (
    CodexOAuthFlowManager,
    exception_error_message,
)
from coding_agent.server.session_manager import SessionManager

DEVICE_CODE = DeviceCode(
    verification_url="https://auth.openai.com/codex/device",
    user_code="ABCD-1234",
    device_auth_id="device-auth-1",
    interval=1,
)


def make_record(
    *,
    email: str | None = None,
    account_id: str | None = None,
    plan: str | None = "plus",
) -> OAuthProviderRecord:
    return OAuthProviderRecord(
        issuer="https://auth.openai.com",
        client_id="codex-client",
        token_endpoint="https://auth.openai.com/oauth/token",
        tokens=OAuthTokens(access_token="access-token", refresh_token="refresh-token"),
        account=OAuthAccount(
            email=email,
            chatgpt_account_id=account_id,
            chatgpt_plan_type=plan,
        ),
        last_refresh_at=datetime.now(UTC),
    )


def make_client(
    *,
    gate: threading.Event | None = None,
    record: OAuthProviderRecord | None = None,
    request_exc: BaseException | None = None,
    poll_exc: BaseException | None = None,
    exchange_exc: BaseException | None = None,
) -> MagicMock:
    client = MagicMock()
    if request_exc is not None:
        client.request_device_code.side_effect = request_exc
    else:
        client.request_device_code.return_value = DEVICE_CODE
    if poll_exc is not None:
        client.poll_device_token.side_effect = poll_exc
    elif gate is not None:

        def _blocking_poll(*args: Any, **kwargs: Any) -> MagicMock:
            assert gate.wait(timeout=10), "test gate was never released"
            return MagicMock()

        client.poll_device_token.side_effect = _blocking_poll
    else:
        client.poll_device_token.return_value = MagicMock()
    if exchange_exc is not None:
        client.exchange_device_code.side_effect = exchange_exc
    else:
        client.exchange_device_code.return_value = record or make_record()
    return client


@pytest.fixture
async def oauth_env(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_http_session_manager: SessionManager,
):
    """Install an isolated flow manager backed by a tmp store + fake clients."""
    del isolated_http_session_manager
    store = OAuthStore(tmp_path / "auth.json")
    clients: list[MagicMock] = []
    pending_factories: list[MagicMock] = []

    def factory() -> MagicMock:
        client = pending_factories.pop(0) if pending_factories else make_client()
        clients.append(client)
        return client

    manager = CodexOAuthFlowManager(store=store, client_factory=factory)
    monkeypatch.setattr(http_server, "codex_oauth_flow_manager", manager)

    transport = ASGITransport(app=http_server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield SimpleNamespace(
            store=store,
            manager=manager,
            clients=clients,
            pending_factories=pending_factories,
            http=client,
        )


async def wait_for_state(env, flow_id: str, *states: str, timeout: float = 5.0) -> dict:
    import asyncio

    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        response = await env.http.get(f"/oauth/codex/flows/{flow_id}")
        assert response.status_code == 200
        data = response.json()
        if data["state"] in states:
            return data
        assert asyncio.get_running_loop().time() < deadline, (
            f"flow {flow_id} never reached {states}: {data}"
        )
        await asyncio.sleep(0.01)


class TestStartFlow:
    async def test_start_returns_flow_details(self, oauth_env):
        gate = threading.Event()
        oauth_env.pending_factories.append(make_client(gate=gate))
        try:
            response = await oauth_env.http.post("/oauth/codex/start", json={})
            assert response.status_code == 200
            data = response.json()
            assert data["flow_id"]
            assert data["verification_url"] == DEVICE_CODE.verification_url
            assert data["user_code"] == DEVICE_CODE.user_code
            assert data["expires_in"] == oauth_env.manager.ttl_seconds

            flow = await wait_for_state(oauth_env, data["flow_id"], "pending")
            assert flow["state"] == "pending"
        finally:
            gate.set()

    async def test_start_device_code_failure_returns_502_and_no_flow(self, oauth_env):
        oauth_env.pending_factories.append(
            make_client(request_exc=RuntimeError("openai unreachable"))
        )
        response = await oauth_env.http.post("/oauth/codex/start", json={})
        assert response.status_code == 502
        assert "openai unreachable" in response.json()["detail"]

        flows = await oauth_env.http.get("/oauth/codex/flows")
        assert flows.json()["flows"] == []

    async def test_start_rejects_invalid_label(self, oauth_env):
        response = await oauth_env.http.post(
            "/oauth/codex/start", json={"label": "Bad_Label!"}
        )
        assert response.status_code == 422


class TestFlowCompletion:
    async def test_authorized_writes_explicit_label_key(self, oauth_env):
        record = make_record(email="work@example.com")
        oauth_env.pending_factories.append(make_client(record=record))

        response = await oauth_env.http.post(
            "/oauth/codex/start", json={"label": "work"}
        )
        flow_id = response.json()["flow_id"]
        flow = await wait_for_state(oauth_env, flow_id, "authorized")

        assert flow["account_label"] == "work"
        stored = oauth_env.store.get_provider("codex:work")
        assert stored is not None
        assert stored.tokens.access_token == "access-token"

    async def test_authorized_derives_label_from_email(self, oauth_env):
        record = make_record(email="Foo.Bar@Example.com")
        oauth_env.pending_factories.append(make_client(record=record))

        response = await oauth_env.http.post("/oauth/codex/start", json={})
        flow = await wait_for_state(oauth_env, response.json()["flow_id"], "authorized")

        assert flow["account_label"] == "foo-bar-example-com"
        assert oauth_env.store.get_provider("codex:foo-bar-example-com") is not None

    async def test_derived_label_falls_back_to_account_id(self, oauth_env):
        record = make_record(email=None, account_id="9f8e7d6c")
        oauth_env.pending_factories.append(make_client(record=record))

        response = await oauth_env.http.post("/oauth/codex/start", json={})
        flow = await wait_for_state(oauth_env, response.json()["flow_id"], "authorized")

        assert flow["account_label"] == "9f8e7d6c"
        assert oauth_env.store.get_provider("codex:9f8e7d6c") is not None

    async def test_derived_label_conflict_appends_suffix(self, oauth_env):
        oauth_env.store.set_provider(
            "codex:foo-example-com", make_record(email="foo@example.com")
        )
        record = make_record(email="foo@example.com")
        oauth_env.pending_factories.append(make_client(record=record))

        response = await oauth_env.http.post("/oauth/codex/start", json={})
        flow = await wait_for_state(oauth_env, response.json()["flow_id"], "authorized")

        assert flow["account_label"] == "foo-example-com-2"
        assert oauth_env.store.get_provider("codex:foo-example-com-2") is not None

    async def test_explicit_label_relogin_overwrites(self, oauth_env):
        oauth_env.store.set_provider("codex:work", make_record())
        oauth_env.pending_factories.append(make_client())

        response = await oauth_env.http.post(
            "/oauth/codex/start", json={"label": "work"}
        )
        flow = await wait_for_state(oauth_env, response.json()["flow_id"], "authorized")

        assert flow["account_label"] == "work"
        assert "codex:work-2" not in oauth_env.store.load().providers

    async def test_exchange_failure_marks_error(self, oauth_env):
        oauth_env.pending_factories.append(
            make_client(exchange_exc=RuntimeError("bad exchange"))
        )

        response = await oauth_env.http.post("/oauth/codex/start", json={})
        flow = await wait_for_state(oauth_env, response.json()["flow_id"], "error")

        assert flow["error"] == "bad exchange"
        assert oauth_env.store.load().providers == {}

    async def test_poll_timeout_marks_expired(self, oauth_env):
        request = httpx.Request("POST", "https://auth.openai.com/token")
        poll_exc = httpx.HTTPStatusError(
            "forbidden", request=request, response=httpx.Response(403, request=request)
        )
        oauth_env.pending_factories.append(make_client(poll_exc=poll_exc))

        response = await oauth_env.http.post("/oauth/codex/start", json={})
        flow = await wait_for_state(oauth_env, response.json()["flow_id"], "expired")

        assert flow["state"] == "expired"


class TestFlowManagement:
    async def test_cancel_flow(self, oauth_env):
        gate = threading.Event()
        oauth_env.pending_factories.append(make_client(gate=gate))
        try:
            response = await oauth_env.http.post("/oauth/codex/start", json={})
            flow_id = response.json()["flow_id"]
            await wait_for_state(oauth_env, flow_id, "pending")

            cancel = await oauth_env.http.post(f"/oauth/codex/flows/{flow_id}/cancel")
            assert cancel.status_code == 200
            assert cancel.json()["state"] == "cancelled"

            flow = await oauth_env.http.get(f"/oauth/codex/flows/{flow_id}")
            assert flow.json()["state"] == "cancelled"
        finally:
            gate.set()
        assert oauth_env.store.load().providers == {}

    async def test_unknown_flow_returns_404(self, oauth_env):
        response = await oauth_env.http.get("/oauth/codex/flows/nope")
        assert response.status_code == 404
        cancel = await oauth_env.http.post("/oauth/codex/flows/nope/cancel")
        assert cancel.status_code == 404

    async def test_concurrent_flows_are_independent(self, oauth_env):
        gate_a = threading.Event()
        gate_b = threading.Event()
        oauth_env.pending_factories.append(make_client(gate=gate_a))
        oauth_env.pending_factories.append(
            make_client(gate=gate_b, record=make_record(email="b@example.com"))
        )
        try:
            flow_a = (
                await oauth_env.http.post("/oauth/codex/start", json={"label": "a"})
            ).json()["flow_id"]
            flow_b = (
                await oauth_env.http.post("/oauth/codex/start", json={"label": "b"})
            ).json()["flow_id"]
            assert flow_a != flow_b

            await oauth_env.http.post(f"/oauth/codex/flows/{flow_a}/cancel")
            gate_b.set()
            final_b = await wait_for_state(oauth_env, flow_b, "authorized")
            final_a = (await oauth_env.http.get(f"/oauth/codex/flows/{flow_a}")).json()

            assert final_a["state"] == "cancelled"
            assert final_b["state"] == "authorized"
            assert final_b["account_label"] == "b"

            flows = (await oauth_env.http.get("/oauth/codex/flows")).json()["flows"]
            assert {f["flow_id"] for f in flows} == {flow_a, flow_b}
        finally:
            gate_a.set()
            gate_b.set()


class TestAccounts:
    async def test_list_accounts(self, oauth_env):
        oauth_env.store.set_provider("codex", make_record(email="main@example.com"))
        oauth_env.store.set_provider(
            "codex:work",
            make_record(email="work@example.com", plan="team"),
        )
        oauth_env.store.set_provider("copilot", make_record(email="nope@example.com"))

        response = await oauth_env.http.get("/oauth/accounts")
        assert response.status_code == 200
        accounts = {a["provider"]: a for a in response.json()["accounts"]}

        assert set(accounts) == {"codex", "codex:work"}
        assert accounts["codex"]["label"] == "default"
        assert accounts["codex"]["email"] == "main@example.com"
        assert accounts["codex"]["plan"] == "plus"
        assert accounts["codex"]["connected_at"] is not None
        assert accounts["codex:work"]["label"] == "work"
        assert accounts["codex:work"]["plan"] == "team"

    async def test_delete_account(self, oauth_env):
        oauth_env.store.set_provider("codex:work", make_record())

        response = await oauth_env.http.delete("/oauth/accounts/codex:work")
        assert response.status_code == 200
        assert response.json() == {"status": "deleted", "provider": "codex:work"}
        assert oauth_env.store.get_provider("codex:work") is None

        missing = await oauth_env.http.delete("/oauth/accounts/codex:work")
        assert missing.status_code == 404

    async def test_delete_rejects_non_codex_key(self, oauth_env):
        response = await oauth_env.http.delete("/oauth/accounts/anthropic")
        assert response.status_code == 400


class TestSessionProviderRouting:
    async def test_create_session_with_connected_codex_label(self, oauth_env):
        oauth_env.store.set_provider("codex:work", make_record())
        response = await oauth_env.http.post(
            "/sessions",
            json={"provider": "codex:work", "model": "gpt-5.5"},
        )
        assert response.status_code == 200
        assert response.json()["session_id"]

    async def test_create_session_with_unconnected_codex_label_returns_400(
        self, oauth_env
    ):
        response = await oauth_env.http.post(
            "/sessions",
            json={"provider": "codex:missing", "model": "gpt-5.5"},
        )
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "codex account not connected: codex:missing" in detail
        assert "/oauth/codex/start" in detail

    async def test_create_session_rejects_invalid_codex_label(self, oauth_env):
        response = await oauth_env.http.post(
            "/sessions",
            json={"provider": "codex:Bad_Label!", "model": "gpt-5.5"},
        )
        assert response.status_code == 422

    async def test_create_session_rejects_unknown_provider(self, oauth_env):
        response = await oauth_env.http.post(
            "/sessions",
            json={"provider": "not-a-provider", "model": "x"},
        )
        assert response.status_code == 422


class TestHelpers:
    def test_exception_error_message_falls_back_to_class_name(self):
        assert exception_error_message(RuntimeError("boom")) == "boom"
        assert exception_error_message(RuntimeError()) == "RuntimeError"
