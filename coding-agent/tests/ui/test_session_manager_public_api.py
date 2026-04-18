from __future__ import annotations
import asyncio
import threading
import types
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from agentkit.errors import ConfigError
from agentkit.checkpoint import CheckpointService
from agentkit.checkpoint.models import CheckpointMeta
from agentkit.tape.tape import Tape
from coding_agent.approval.store import ApprovalStore
from coding_agent.approval import ApprovalPolicy
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
    PGSessionMetadataStore,
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


def test_session_manager_accepts_injected_storage_backends() -> None:
    class FakeTapeStore:
        async def save(self, tape_id: str, entries: list[dict[str, object]]) -> None:
            return None

        async def load(self, tape_id: str) -> list[dict[str, object]]:
            return []

        async def list_ids(self) -> list[str]:
            return []

        async def truncate(self, tape_id: str, keep: int) -> None:
            return None

    class FakeCheckpointStore:
        async def save(self, snapshot) -> None:
            raise AssertionError("unused")

        async def load(self, checkpoint_id: str):
            raise AssertionError("unused")

        async def list_by_tape(self, tape_id: str):
            return []

        async def delete(self, checkpoint_id: str) -> None:
            return None

    tape_store = FakeTapeStore()
    checkpoint_service = CheckpointService(FakeCheckpointStore())

    manager = SessionManager(
        store=InMemorySessionStore(),
        tape_store=tape_store,
        checkpoint_service=checkpoint_service,
    )

    assert manager._tape_store is tape_store
    assert manager._checkpoint_service is checkpoint_service


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


@pytest.mark.asyncio
async def test_create_session_persists_explicit_provider_restart_metadata() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    provider = MockProvider()

    session_id = await manager.create_session(
        provider=provider,
        provider_name="openai",
        model_name="gpt-test",
        base_url="http://localhost:1234/v1",
    )
    payload = store.get(session_id)

    assert payload is not None
    assert payload["provider_name"] == "openai"
    assert payload["model_name"] == "gpt-test"
    assert payload["base_url"] == "http://localhost:1234/v1"


@pytest.mark.asyncio
async def test_create_session_persists_configured_restart_metadata_by_default() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)

    with patch("coding_agent.core.config.load_config") as load_config:
        load_config.return_value = types.SimpleNamespace(
            provider="anthropic",
            model="claude-test",
            base_url="http://llm.default",
        )

        session_id = await manager.create_session(approval_policy=ApprovalPolicy.AUTO)

    payload = store.get(session_id)

    assert payload is not None
    assert payload["provider_name"] == "anthropic"
    assert payload["model_name"] == "claude-test"
    assert payload["base_url"] == "http://llm.default"


def test_session_manager_uses_pg_backends_when_storage_config_requests_pg() -> None:
    class FakePGPool:
        instances: list[FakePGPool] = []

        def __init__(self, *, dsn: str) -> None:
            self.dsn = dsn
            self.__class__.instances.append(self)

        async def get_pool(self) -> FakePGPool:
            return self

        async def close(self) -> None:
            return None

    class FakePGTapeStore:
        def __init__(self, *, pool: FakePGPool) -> None:
            self.pool = pool

    class FakePGCheckpointStore:
        def __init__(self, *, pool: FakePGPool) -> None:
            self.pool = pool

    with (
        patch("coding_agent.ui.session_manager.create_session_store") as create_store,
        patch(
            "coding_agent.ui.session_manager._load_pg_storage_types",
            return_value=(FakePGPool, FakePGTapeStore, FakePGCheckpointStore),
        ),
    ):
        create_store.return_value = PGSessionMetadataStore(
            pool=FakePGPool(dsn="postgresql://example")
        )
        manager = SessionManager(
            storage_config={
                "tape_backend": "pg",
                "checkpoint_backend": "pg",
                "http_session_backend": "pg",
                "dsn": "postgresql://example",
            }
        )

    assert isinstance(manager._store, PGSessionMetadataStore)
    assert isinstance(manager._tape_store, FakePGTapeStore)
    assert isinstance(manager._checkpoint_service._store, FakePGCheckpointStore)
    assert manager._tape_store.pool is manager._checkpoint_service._store.pool
    assert manager._store._pool is not manager._tape_store.pool
    assert len(FakePGPool.instances) == 2


def test_session_manager_uses_dedicated_pg_pool_for_http_session_store() -> None:
    class FakePGPool:
        instances: list[FakePGPool] = []

        def __init__(self, *, dsn: str) -> None:
            self.dsn = dsn
            self.__class__.instances.append(self)

    class FakePGTapeStore:
        def __init__(self, *, pool: FakePGPool) -> None:
            self.pool = pool

    class FakePGCheckpointStore:
        def __init__(self, *, pool: FakePGPool) -> None:
            self.pool = pool

    with (
        patch("coding_agent.ui.session_manager.create_session_store") as create_store,
        patch(
            "coding_agent.ui.session_manager._load_pg_storage_types",
            return_value=(FakePGPool, FakePGTapeStore, FakePGCheckpointStore),
        ),
    ):
        create_store.return_value = InMemorySessionStore()
        _ = SessionManager(
            storage_config={
                "tape_backend": "pg",
                "checkpoint_backend": "pg",
                "http_session_backend": "pg",
                "dsn": "postgresql://example",
            }
        )

    assert create_store.call_count == 1
    assert create_store.call_args.kwargs["backend"] == "pg"
    assert create_store.call_args.kwargs["dsn"] == "postgresql://example"
    assert create_store.call_args.kwargs["pg_pool"] is None
    assert len(FakePGPool.instances) == 1


def test_session_manager_normalizes_tape_backend_for_http_session_pg_default() -> None:
    manager = SessionManager.__new__(SessionManager)
    manager._storage_config = {
        "tape_backend": " PG ",
        "dsn": "postgresql://example",
    }
    manager._pg_pool = None

    class FakePGPool:
        def __init__(self, *, dsn: str) -> None:
            self.dsn = dsn

    with (
        patch("coding_agent.ui.session_manager.create_session_store") as create_store,
        patch(
            "coding_agent.ui.session_manager._load_pg_storage_types",
            return_value=(FakePGPool, object(), object()),
        ),
    ):
        create_store.return_value = InMemorySessionStore()

        _ = SessionManager._create_http_session_store(manager)

    assert create_store.call_args.kwargs["backend"] == "pg"


def test_session_manager_normalizes_tape_backend_for_checkpoint_default() -> None:
    manager = SessionManager.__new__(SessionManager)
    manager._storage_config = {
        "tape_backend": " PG ",
        "dsn": "postgresql://example",
    }

    class FakePGPool:
        def __init__(self, *, dsn: str) -> None:
            self.dsn = dsn

    class FakePGCheckpointStore:
        def __init__(self, *, pool: FakePGPool) -> None:
            self.pool = pool

    manager._pg_pool = None

    with patch(
        "coding_agent.ui.session_manager._load_pg_storage_types",
        return_value=(FakePGPool, object(), FakePGCheckpointStore),
    ):
        store = SessionManager._create_checkpoint_store(manager, Path("/tmp/data"))

    assert isinstance(store, FakePGCheckpointStore)


def test_session_manager_strips_dsn_before_creating_pg_pool() -> None:
    class FakePGPool:
        def __init__(self, *, dsn: str) -> None:
            self.dsn = dsn

    manager = SessionManager.__new__(SessionManager)
    manager._storage_config = {"dsn": "  postgresql://example  "}
    manager._pg_pool = None

    with patch(
        "coding_agent.ui.session_manager._load_pg_storage_types",
        return_value=(FakePGPool, object(), object()),
    ):
        pool = SessionManager._get_pg_pool(manager)

    assert isinstance(pool, FakePGPool)
    assert pool.dsn == "postgresql://example"


@pytest.mark.asyncio
async def test_remove_session_async_loads_store_once_when_not_cached() -> None:
    class CountingStore(InMemorySessionStore):
        def __init__(self) -> None:
            super().__init__()
            self.load_calls = 0

        def load(self, session_id: str):
            self.load_calls += 1
            return super().load(session_id)

    store = CountingStore()
    manager = SessionManager(store=store)
    session = Session(
        id="remove-me",
        created_at=datetime.now(),
        last_activity=datetime.now(),
        approval_store=ApprovalStore(),
    )
    store.save(session.id, session.to_store_data())

    await manager.remove_session_async("remove-me")

    assert store.load_calls == 1
    assert store.load("remove-me") is None


@pytest.mark.asyncio
async def test_close_offloads_sync_store_close_to_executor() -> None:
    close_called = threading.Event()
    loop_thread_id = threading.get_ident()
    close_thread_ids: list[int] = []

    class ClosableStore(InMemorySessionStore):
        def close(self) -> None:
            close_thread_ids.append(threading.get_ident())
            close_called.set()

    manager = SessionManager(store=ClosableStore())

    await manager.close()

    assert close_called.is_set() is True
    assert close_thread_ids == [close_thread_ids[0]]
    assert close_thread_ids[0] != loop_thread_id


@pytest.mark.asyncio
async def test_shutdown_session_runtime_preserves_persisted_session_metadata() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.turn_in_progress = True
    session.runtime_pipeline = object()
    session.runtime_ctx = object()

    class FakeAdapter:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    adapter = FakeAdapter()
    session.runtime_adapter = adapter

    await manager.shutdown_session_runtime(session_id)

    payload = store.get(session_id)
    assert payload is not None
    assert payload["id"] == session_id
    assert adapter.closed is True
    reloaded = manager.get_session(session_id)
    assert reloaded.turn_in_progress is False
    assert reloaded.runtime_pipeline is None
    assert reloaded.runtime_ctx is None
    assert reloaded.runtime_adapter is None


@pytest.mark.asyncio
async def test_capture_checkpoint_persists_tape_id_via_async_store_path() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    runtime_ctx = types.SimpleNamespace(tape=Tape(tape_id="stable-tape"))
    session.runtime_pipeline = object()
    session.runtime_ctx = runtime_ctx
    session.runtime_adapter = object()

    expected = CheckpointMeta(
        checkpoint_id="cp-save",
        tape_id="stable-tape",
        session_id=session_id,
        entry_count=0,
        window_start=0,
        created_at=datetime.now(),
        label="manual save",
    )
    persist_calls: list[str] = []

    class FakeCheckpointService:
        async def capture(self, ctx, label: str | None = None, extra=None):
            del label, extra
            assert ctx is runtime_ctx
            return expected

    async def fake_persist_session_async(current_session: Session) -> None:
        persist_calls.append(current_session.id)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(
            manager,
            "_persist_session_async",
            fake_persist_session_async,
            raising=False,
        )
        checkpoint = await manager.capture_checkpoint(session_id, label="manual save")

    assert checkpoint == expected
    assert persist_calls == [session_id]


@pytest.mark.asyncio
async def test_cleanup_idle_sessions_uses_async_store_helpers() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    first_id = await manager.create_session()
    second_id = await manager.create_session()

    first = manager.get_session(first_id)
    second = manager.get_session(second_id)
    first.last_activity = datetime.now()
    second.last_activity = datetime.now()

    sync_calls: list[str] = []
    async_calls: list[str] = []
    closed_sessions: list[str] = []

    def fail_sync_list_sessions() -> list[str]:
        sync_calls.append("list")
        raise AssertionError("cleanup_idle_sessions should not call sync list_sessions")

    def fail_sync_get_session(_session_id: str) -> Session:
        sync_calls.append("get")
        raise AssertionError("cleanup_idle_sessions should not call sync get_session")

    async def fake_list_sessions_async() -> list[str]:
        async_calls.append("list")
        return [first_id, second_id]

    async def fake_get_session_async(session_id: str) -> Session:
        async_calls.append(f"get:{session_id}")
        return first if session_id == first_id else second

    async def fake_close_session(session_id: str) -> None:
        closed_sessions.append(session_id)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(manager._store, "list_sessions", fail_sync_list_sessions)
        mp.setattr(manager, "get_session", fail_sync_get_session)
        mp.setattr(manager, "list_sessions_async", fake_list_sessions_async)
        mp.setattr(manager, "get_session_async", fake_get_session_async)
        mp.setattr(manager, "close_session", fake_close_session)

        closed = await manager.cleanup_idle_sessions(max_idle_minutes=10_000)

    assert closed == []
    assert closed_sessions == []
    assert sync_calls == []
    assert async_calls == ["list", f"get:{first_id}", f"get:{second_id}"]


def test_create_session_store_warns_and_falls_back_when_redis_unreachable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def failing_factory(url: str):
        raise OSError(f"cannot connect to {url}")

    with caplog.at_level("WARNING"):
        store = create_session_store(
            redis_url="redis://:supersecret@example:6379/0",
            redis_client_factory=failing_factory,
        )

    assert isinstance(store, InMemorySessionStore)
    assert "falling back to in-memory" in caplog.text
    assert "supersecret" not in caplog.text
    assert "redis://example:6379/0" in caplog.text


def test_create_session_store_redacts_reformatted_redis_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def failing_factory(url: str):
        raise OSError("AUTH failed for redis://:supersecret@example:6379/0")

    with caplog.at_level("WARNING"):
        store = create_session_store(
            redis_url="redis://user:supersecret@example:6379/0",
            redis_client_factory=failing_factory,
        )

    assert isinstance(store, InMemorySessionStore)
    assert "supersecret" not in caplog.text
    assert "redis://example:6379/0" in caplog.text


def test_create_session_store_redacts_unix_socket_redis_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def failing_factory(url: str):
        raise OSError(f"cannot connect to {url}")

    with caplog.at_level("WARNING"):
        store = create_session_store(
            redis_url="redis://:supersecret@/tmp/redis.sock",
            redis_client_factory=failing_factory,
        )

    assert isinstance(store, InMemorySessionStore)
    assert "supersecret" not in caplog.text
    assert "redis:/tmp/redis.sock" in caplog.text


def test_create_session_store_redacts_ipv6_redis_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def failing_factory(url: str):
        raise OSError(f"cannot connect to {url}")

    with caplog.at_level("WARNING"):
        store = create_session_store(
            redis_url="redis://:supersecret@[2001:db8::1]:6379/0",
            redis_client_factory=failing_factory,
        )

    assert isinstance(store, InMemorySessionStore)
    assert "supersecret" not in caplog.text
    assert "redis://[2001:db8::1]:6379/0" in caplog.text


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
    fake_ctx = types.SimpleNamespace(config={}, tape=Tape())
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
    fake_ctx = types.SimpleNamespace(config={}, tape=Tape())

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
    fake_ctx = types.SimpleNamespace(config={}, tape=Tape())

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


@pytest.mark.asyncio
async def test_list_checkpoints_returns_metadata_for_session_tape() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.tape_id = "stable-tape"
    manager.register_session(session)

    expected = CheckpointMeta(
        checkpoint_id="cp-1",
        tape_id="stable-tape",
        session_id=session_id,
        entry_count=3,
        window_start=1,
        created_at=datetime.now(),
        label="before-restore",
    )

    class FakeCheckpointService:
        async def list(self, tape_id: str):
            assert tape_id == "stable-tape"
            return [expected]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        checkpoints = await manager.list_checkpoints(session_id)

    assert checkpoints == [expected]


@pytest.mark.asyncio
async def test_restore_checkpoint_rejects_active_turn() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.tape_id = "stable-tape"
    session.turn_in_progress = True
    manager.register_session(session)

    with pytest.raises(RuntimeError, match="turn already in progress"):
        await manager.restore_checkpoint(session_id, "cp-active")


@pytest.mark.asyncio
async def test_restore_checkpoint_rejects_when_turn_lock_is_held() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.tape_id = "stable-tape"
    manager.register_session(session)

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("restore should not run while turn lock is held")

    turn_lock = manager._turn_lock_for(session_id)
    async with turn_lock:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(manager, "_restore_checkpoint", fail_if_called, raising=False)
            with pytest.raises(RuntimeError, match="turn already in progress"):
                await manager.restore_checkpoint(session_id, "cp-locked")


@pytest.mark.asyncio
async def test_capture_checkpoint_uses_current_runtime_context() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    runtime_ctx = types.SimpleNamespace(tape=Tape(tape_id="stable-tape"))
    session.runtime_pipeline = object()
    session.runtime_ctx = runtime_ctx
    session.runtime_adapter = object()

    expected = CheckpointMeta(
        checkpoint_id="cp-save",
        tape_id="stable-tape",
        session_id=session_id,
        entry_count=0,
        window_start=0,
        created_at=datetime.now(),
        label="manual save",
    )

    class FakeCheckpointService:
        async def capture(self, ctx, label: str | None = None, extra=None):
            assert ctx is runtime_ctx
            assert label == "manual save"
            assert extra == {
                "session_restart_config": {
                    "provider_name": session.provider_name,
                    "model_name": session.model_name,
                    "base_url": session.base_url,
                    "max_steps": session.max_steps,
                    "approval_policy": session.approval_policy.value,
                }
            }
            return expected

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        checkpoint = await manager.capture_checkpoint(session_id, label="manual save")

    assert checkpoint == expected


@pytest.mark.asyncio
async def test_capture_checkpoint_stamps_restart_safe_session_config_into_extra() -> (
    None
):
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session(
        provider_name="anthropic",
        model_name="claude-checkpoint",
        base_url="http://checkpoint.local",
        max_steps=17,
        approval_policy=ApprovalPolicy.INTERACTIVE,
    )
    session = manager.get_session(session_id)
    runtime_ctx = types.SimpleNamespace(tape=Tape(tape_id="stable-tape"))
    session.runtime_pipeline = object()
    session.runtime_ctx = runtime_ctx
    session.runtime_adapter = object()

    expected = CheckpointMeta(
        checkpoint_id="cp-save",
        tape_id="stable-tape",
        session_id=session_id,
        entry_count=0,
        window_start=0,
        created_at=datetime.now(),
        label="manual save",
    )
    captured_extra: dict[str, object] | None = None

    class FakeCheckpointService:
        async def capture(self, ctx, label: str | None = None, extra=None):
            nonlocal captured_extra
            assert ctx is runtime_ctx
            assert label == "manual save"
            assert extra is not None
            captured_extra = dict(extra)
            return expected

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        checkpoint = await manager.capture_checkpoint(
            session_id,
            label="manual save",
            extra={"workspace": "/tmp/repo"},
        )

    assert checkpoint == expected
    assert captured_extra == {
        "workspace": "/tmp/repo",
        "session_restart_config": {
            "provider_name": "anthropic",
            "model_name": "claude-checkpoint",
            "base_url": "http://checkpoint.local",
            "max_steps": 17,
            "approval_policy": "interactive",
        },
    }


@pytest.mark.asyncio
async def test_capture_checkpoint_rejects_reserved_restart_config_key() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.runtime_pipeline = object()
    session.runtime_ctx = types.SimpleNamespace(tape=Tape(tape_id="stable-tape"))
    session.runtime_adapter = object()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            manager,
            "_checkpoint_service",
            types.SimpleNamespace(capture=lambda *args, **kwargs: None),
            raising=False,
        )

        with pytest.raises(ValueError, match="reserved checkpoint metadata key"):
            await manager.capture_checkpoint(
                session_id,
                extra={"session_restart_config": {"provider_name": "oops"}},
            )


@pytest.mark.asyncio
async def test_capture_checkpoint_rejects_active_turn() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.turn_in_progress = True

    with pytest.raises(RuntimeError, match="turn already in progress"):
        await manager.capture_checkpoint(session_id, label="manual save")


@pytest.mark.asyncio
async def test_capture_checkpoint_rejects_when_turn_lock_is_held() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    turn_lock = manager._turn_lock_for(session_id)

    await turn_lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="turn already in progress"):
            await manager.capture_checkpoint(session_id, label="manual save")
    finally:
        turn_lock.release()


@pytest.mark.asyncio
async def test_ensure_session_runtime_bootstraps_runtime_and_persists_tape_id() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()

    initialized: list[str] = []
    fake_ctx = types.SimpleNamespace(
        config={"tool_registry": object()},
        tape=Tape(tape_id="bootstrapped-tape"),
        plugin_states={},
    )
    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        ),
        _directive_executor=None,
    )

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            initialized.append("initialized")

    def fake_create_agent(**kwargs):
        assert kwargs["session_id_override"] == session_id
        return fake_pipeline, fake_ctx

    with (
        patch("importlib.import_module") as import_module,
        patch.dict(
            SessionManager.ensure_session_runtime.__globals__,
            {"PipelineAdapter": FakeAdapter},
        ),
    ):
        import_module.return_value = types.SimpleNamespace(
            create_agent=fake_create_agent
        )
        returned_ctx = await manager.ensure_session_runtime(session_id)

    session = manager.get_session(session_id)
    payload = store.get(session_id)

    assert returned_ctx is fake_ctx
    assert session.runtime_ctx is fake_ctx
    assert payload is not None
    assert payload["tape_id"] == "bootstrapped-tape"
    assert initialized == ["initialized"]
