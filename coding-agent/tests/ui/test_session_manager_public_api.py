from __future__ import annotations
import types
from datetime import datetime
from unittest.mock import patch

import pytest

from coding_agent.approval.store import ApprovalStore
from coding_agent.wire.protocol import (
    ApprovalRequest,
    CompletionStatus,
    StreamDelta,
    ToolCallDelta,
    TurnEnd,
)
from coding_agent.ui.session_manager import MockProvider, Session, SessionManager
from coding_agent.ui.session_store import (
    InMemorySessionStore,
    RedisSessionStore,
    create_session_store,
)


def test_register_session_uses_public_api() -> None:
    manager = SessionManager()
    approval_store = ApprovalStore()
    session = Session(
        id="test-session",
        created_at=datetime.now(),
        last_activity=datetime.now(),
        approval_store=approval_store,
    )

    manager.register_session(session)

    assert manager.has_session("test-session")
    assert manager.get_session("test-session") is session


def test_clear_sessions_uses_public_api() -> None:
    manager = SessionManager()
    session = Session(
        id="test-session",
        created_at=datetime.now(),
        last_activity=datetime.now(),
        approval_store=ApprovalStore(),
    )
    manager.register_session(session)

    manager.clear_sessions()

    assert manager.list_sessions() == []


@pytest.mark.asyncio
async def test_create_session_persists_to_store_backing() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)

    session_id = await manager.create_session()
    payload = store.get(session_id)

    assert manager.has_session(session_id)
    assert payload is not None
    assert payload["id"] == session_id
    assert store.list_sessions() == [session_id]


def test_create_session_store_warns_and_falls_back_when_redis_unreachable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def failing_factory(url: str):
        raise OSError(f"cannot connect to {url}")

    with caplog.at_level("WARNING"):
        store = create_session_store(
            redis_url="redis://example:6379/0",
            redis_client_factory=failing_factory,
        )

    assert isinstance(store, InMemorySessionStore)
    assert "falling back to in-memory" in caplog.text


def test_in_memory_session_store_reports_healthy() -> None:
    assert InMemorySessionStore().check_health() is True


def test_redis_session_store_can_rehydrate_session_metadata() -> None:
    class FakeRedisClient:
        def __init__(self) -> None:
            self._data: dict[str, str] = {}
            self._index: set[str] = set()

        def ping(self) -> bool:
            return True

        def set(self, key: str, value: str) -> None:
            self._data[key] = value

        def get(self, key: str) -> str | None:
            return self._data.get(key)

        def delete(self, key: str) -> None:
            self._data.pop(key, None)

        def sadd(self, key: str, value: str) -> None:
            assert key == "coding-agent:sessions:index"
            self._index.add(value)

        def srem(self, key: str, value: str) -> None:
            assert key == "coding-agent:sessions:index"
            self._index.discard(value)

        def smembers(self, key: str) -> set[str]:
            assert key == "coding-agent:sessions:index"
            return set(self._index)

    client = FakeRedisClient()
    store = RedisSessionStore(client=client, redis_url="redis://test")
    session = Session(
        id="persisted-session",
        created_at=datetime.now(),
        last_activity=datetime.now(),
        approval_store=ApprovalStore(),
    )

    store.save(session.id, session.to_store_data())

    payload = RedisSessionStore(client=client, redis_url="redis://test").load(
        "persisted-session"
    )

    assert payload is not None
    assert payload["id"] == "persisted-session"
    assert "persisted-session" in store.list_sessions()


def test_rehydrate_clears_non_restart_safe_runtime_state() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session = Session(
        id="rehydrate-me",
        created_at=datetime.now(),
        last_activity=datetime.now(),
        approval_store=ApprovalStore(),
        turn_in_progress=True,
        pending_approval={"request_id": "req-123", "tool_name": "bash"},
        approval_response={"decision": "approve", "feedback": "ok"},
    )
    manager.register_session(session)

    reloaded = SessionManager(store=store).get_session("rehydrate-me")

    assert reloaded.turn_in_progress is False
    assert reloaded.pending_approval is None
    assert reloaded.approval_response is None


@pytest.mark.asyncio
async def test_run_agent_restores_restart_safe_agent_configuration_after_rehydrate() -> (
    None
):
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session = Session(
        id="rehydrate-configured-session",
        created_at=datetime.now(),
        last_activity=datetime.now(),
        approval_store=ApprovalStore(),
        provider=MockProvider(),
        max_steps=9,
    )
    session.provider_name = "anthropic"
    session.model_name = "claude-test"
    session.base_url = "http://llm.local"
    manager.register_session(session)

    rehydrated_manager = SessionManager(store=store)
    rehydrated_session = rehydrated_manager.get_session("rehydrate-configured-session")

    assert rehydrated_session.provider is None

    llm_plugin = types.SimpleNamespace(_instance=None)
    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(get=lambda _: llm_plugin),
        _directive_executor=None,
    )
    fake_ctx = types.SimpleNamespace(config={})
    captured_kwargs: dict[str, object] = {}

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt

    def fake_create_agent(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_pipeline, fake_ctx

    with (
        patch("importlib.import_module") as import_module,
        patch.dict(
            SessionManager.run_agent.__globals__, {"PipelineAdapter": FakeAdapter}
        ),
    ):
        import_module.return_value = types.SimpleNamespace(
            create_agent=fake_create_agent
        )
        await rehydrated_manager.run_agent("rehydrate-configured-session", "hello")

    assert captured_kwargs["provider_override"] == "anthropic"
    assert captured_kwargs["model_override"] == "claude-test"
    assert captured_kwargs["base_url_override"] == "http://llm.local"
    assert captured_kwargs["max_steps_override"] == 9
    assert llm_plugin._instance is None


def test_redis_session_store_reports_health_from_ping() -> None:
    class FakeRedisClient:
        def ping(self) -> bool:
            return True

        def set(self, key: str, value: str) -> None:
            raise AssertionError("unused")

        def get(self, key: str) -> str | None:
            raise AssertionError("unused")

        def delete(self, key: str) -> None:
            raise AssertionError("unused")

        def sadd(self, key: str, value: str) -> None:
            raise AssertionError("unused")

        def srem(self, key: str, value: str) -> None:
            raise AssertionError("unused")

        def smembers(self, key: str) -> set[str]:
            raise AssertionError("unused")

    store = RedisSessionStore(client=FakeRedisClient(), redis_url="redis://test")

    assert store.check_health() is True


def test_redis_session_store_reports_unhealthy_on_ping_error() -> None:
    class FailingRedisClient:
        def __init__(self) -> None:
            self._first = True

        def ping(self) -> bool:
            if self._first:
                self._first = False
                return True
            raise OSError("redis unavailable")

        def set(self, key: str, value: str) -> None:
            raise AssertionError("unused")

        def get(self, key: str) -> str | None:
            raise AssertionError("unused")

        def delete(self, key: str) -> None:
            raise AssertionError("unused")

        def sadd(self, key: str, value: str) -> None:
            raise AssertionError("unused")

        def srem(self, key: str, value: str) -> None:
            raise AssertionError("unused")

        def smembers(self, key: str) -> set[str]:
            raise AssertionError("unused")

    store = RedisSessionStore(client=FailingRedisClient(), redis_url="redis://test")

    assert store.check_health() is False


@pytest.mark.asyncio
async def test_run_agent_does_not_hardcode_api_key() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        ),
        _directive_executor=None,
    )
    fake_ctx = types.SimpleNamespace(config={})

    with (
        patch("importlib.import_module") as import_module,
        patch("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter),
    ):
        import_module.return_value = types.SimpleNamespace(
            create_agent=lambda **kwargs: (fake_pipeline, fake_ctx, kwargs)
        )

        captured_kwargs: dict[str, object] = {}

        def fake_create_agent(**kwargs):
            captured_kwargs.update(kwargs)
            return fake_pipeline, fake_ctx

        import_module.return_value = types.SimpleNamespace(
            create_agent=fake_create_agent
        )

        await manager.run_agent(session_id, "hello")

    assert captured_kwargs["session_id_override"] == session_id
    assert captured_kwargs["api_key"] is None


@pytest.mark.asyncio
async def test_run_agent_emits_error_turn_end_when_bootstrap_fails() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)

    with patch("importlib.import_module") as import_module:
        import_module.return_value = types.SimpleNamespace(
            create_agent=lambda **kwargs: (_ for _ in ()).throw(
                RuntimeError("bootstrap exploded")
            )
        )

        await manager.run_agent(session_id, "hello")

    first = await session.wire.get_next_outgoing()
    second = await session.wire.get_next_outgoing()

    assert isinstance(first, StreamDelta)
    assert first.session_id == session_id
    assert first.agent_id == ""
    assert "bootstrap exploded" in first.content

    assert isinstance(second, TurnEnd)
    assert second.session_id == session_id
    assert second.agent_id == ""
    assert second.completion_status is CompletionStatus.ERROR
    assert session.turn_in_progress is False


@pytest.mark.asyncio
async def test_run_agent_clears_pending_approval_after_runtime_timeout() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session(provider=MockProvider())
    session = manager.get_session(session_id)
    manager.register_session(session)

    req = ApprovalRequest(
        session_id=session_id,
        request_id="req-timeout",
        tool_call=ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={"command": "pwd"},
            call_id="call-timeout",
        ),
        timeout_seconds=0,
    )

    runtime_consumer = None
    approval_requested = False

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, ctx
            nonlocal runtime_consumer
            runtime_consumer = consumer

        async def run_turn(self, prompt: str) -> None:
            del prompt
            nonlocal approval_requested
            assert runtime_consumer is not None
            approval_requested = True
            await runtime_consumer.request_approval(req)

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        ),
        _directive_executor=None,
    )
    fake_ctx = types.SimpleNamespace(config={})

    with (
        patch("importlib.import_module") as import_module,
        patch.dict(
            SessionManager.run_agent.__globals__, {"PipelineAdapter": FakeAdapter}
        ),
    ):
        import_module.return_value = types.SimpleNamespace(
            create_agent=lambda **kwargs: (fake_pipeline, fake_ctx)
        )
        await manager.run_agent(session_id, "needs approval")

    assert approval_requested is True
    reloaded = manager.get_session(session_id)
    assert reloaded.pending_approval is None
    assert reloaded.approval_response is None
    assert reloaded.approval_store.get_request("req-timeout") is None


@pytest.mark.asyncio
async def test_submit_approval_rejects_stale_pending_projection_without_store_request() -> (
    None
):
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.pending_approval = {"request_id": "stale-req", "tool_name": "bash"}
    session.approval_event.clear()

    result = await manager.submit_approval(
        session_id=session_id,
        request_id="stale-req",
        approved=True,
        feedback="approve stale projection",
    )

    assert result is False
    assert session.pending_approval == {"request_id": "stale-req", "tool_name": "bash"}
    assert session.approval_response is None
    assert session.approval_event.is_set() is False
