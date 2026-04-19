from __future__ import annotations

import asyncio
import types
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from coding_agent.approval import ApprovalPolicy
from coding_agent.ui.execution_binding import LocalExecutionBinding
from coding_agent.wire.protocol import (
    ApprovalRequest,
    CompletionStatus,
    StreamDelta,
    ToolCallDelta,
    TurnEnd,
)
from coding_agent.ui.session_manager import (
    MockProvider,
    SessionManager,
    _load_pg_storage_types,
)
from coding_agent.ui.session_store import InMemorySessionStore


@pytest.mark.asyncio
async def test_run_agent_does_not_hardcode_api_key() -> None:
    manager = SessionManager()
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
        )
    )
    fake_ctx = types.SimpleNamespace(config={}, tape=Tape())

    captured_kwargs: dict[str, object] = {}

    def fake_create_agent(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_pipeline, fake_ctx

    with (
        pytest.MonkeyPatch.context() as mp,
    ):
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "hello")

    assert captured_kwargs["session_id_override"] == session_id
    assert captured_kwargs["api_key"] is None


def test_load_pg_storage_types_reports_missing_optional_dependencies() -> None:
    with pytest.MonkeyPatch.context() as mp:
        fake_import_error = ModuleNotFoundError("No module named 'asyncpg'")
        mp.setattr(
            "coding_agent.ui.session_manager.importlib.import_module",
            lambda name: (_ for _ in ()).throw(fake_import_error),
        )

        with pytest.raises(RuntimeError, match="optional dependencies"):
            _load_pg_storage_types()


def test_load_pg_storage_types_reports_missing_exports() -> None:
    fake_module = types.SimpleNamespace(PGPool=object(), PGTapeStore=object())

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "coding_agent.ui.session_manager.importlib.import_module",
            lambda name: fake_module,
        )

        with pytest.raises(RuntimeError, match="PGCheckpointStore"):
            _load_pg_storage_types()


@pytest.mark.asyncio
async def test_run_agent_emits_error_turn_end_when_bootstrap_fails() -> None:
    manager = SessionManager()
    session_id = await manager.create_session()
    session = manager.get_session(session_id)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "coding_agent.__main__.create_agent",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("bootstrap exploded")),
        )

        await manager.run_agent(session_id, "hello")

    first = await session.wire.get_next_outgoing()
    second = await session.wire.get_next_outgoing()

    assert isinstance(first, StreamDelta)
    assert first.session_id == session_id
    assert "bootstrap exploded" in first.content

    assert isinstance(second, TurnEnd)
    assert second.session_id == session_id
    assert second.completion_status is CompletionStatus.ERROR
    assert session.turn_in_progress is False


@pytest.mark.asyncio
async def test_run_agent_clears_pending_approval_after_runtime_timeout() -> None:
    manager = SessionManager()
    session_id = await manager.create_session(provider=MockProvider())
    session = manager.get_session(session_id)

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
        )
    )
    fake_ctx = types.SimpleNamespace(config={}, tape=Tape())

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "coding_agent.__main__.create_agent",
            lambda **kwargs: (fake_pipeline, fake_ctx),
        )
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        await manager.run_agent(session_id, "needs approval")

    assert approval_requested is True
    assert session.pending_approval is None
    assert session.approval_response is None
    assert session.approval_store.get_request("req-timeout") is None


@pytest.mark.asyncio
async def test_run_agent_reuses_session_tape_id_across_hot_turns() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()

    recorded_tapes: list[Tape | None] = []

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt
            self.ctx.tape.tape_id = "stable-session-tape"

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        recorded_tapes.append(kwargs.get("tape"))
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape") or Tape()
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "hello")
        await manager.run_agent(session_id, "again")

    assert recorded_tapes[0] is None
    assert len(recorded_tapes) == 1
    persisted_payload = store.get(session_id)
    assert persisted_payload is not None
    assert persisted_payload["tape_id"] == "stable-session-tape"


@pytest.mark.asyncio
async def test_run_agent_reuses_live_runtime_for_hot_turns() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()

    create_agent_calls = 0
    adapter_instances: list[FakeAdapter] = []
    observed_prompts: list[str] = []

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx
            adapter_instances.append(self)

        async def run_turn(self, prompt: str) -> None:
            observed_prompts.append(prompt)

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        ),
        _directive_executor=None,
    )

    def fake_create_agent(**kwargs):
        nonlocal create_agent_calls
        create_agent_calls += 1
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape") or Tape()
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "first")
        await manager.run_agent(session_id, "second")

    assert create_agent_calls == 1
    assert len(adapter_instances) == 1
    assert observed_prompts == ["first", "second"]


@pytest.mark.asyncio
async def test_run_agent_closes_cached_runtime_after_turn_failure() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()

    close_calls: list[str] = []

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt
            raise RuntimeError("turn exploded")

        async def close(self) -> None:
            close_calls.append("closed")

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        ),
        _directive_executor=None,
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape") or Tape()
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        await manager.run_agent(session_id, "boom")

    session = manager.get_session(session_id)
    assert close_calls == ["closed"]
    assert session.runtime_pipeline is None
    assert session.runtime_ctx is None
    assert session.runtime_adapter is None


@pytest.mark.asyncio
async def test_remove_session_async_awaits_runtime_close() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    close_started = asyncio.Event()
    close_released = asyncio.Event()

    class FakeAdapter:
        async def close(self) -> None:
            close_started.set()
            await close_released.wait()

    session.runtime_pipeline = object()
    session.runtime_ctx = object()
    session.runtime_adapter = FakeAdapter()

    task = asyncio.create_task(manager.remove_session_async(session_id))
    await asyncio.wait_for(close_started.wait(), timeout=1)

    assert task.done() is False
    assert manager.has_session(session_id) is True

    close_released.set()
    await task

    assert manager.has_session(session_id) is False


@pytest.mark.asyncio
async def test_close_session_raises_if_task_survives_cancellation() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)

    class FakeTask:
        def __init__(self) -> None:
            self.cancel_calls = 0

        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            self.cancel_calls += 1

    fake_task = FakeTask()
    session.task = cast(asyncio.Task[None], cast(object, fake_task))

    with patch(
        "coding_agent.ui.session_manager.asyncio.wait_for",
        side_effect=asyncio.TimeoutError,
    ):
        with pytest.raises(RuntimeError, match="did not stop after cancellation"):
            await manager.close_session(session_id)

    assert manager.has_session(session_id) is True
    assert fake_task.cancel_calls == 1


@pytest.mark.asyncio
async def test_shutdown_session_runtime_raises_if_task_survives_cancellation() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)

    class FakeTask:
        def __init__(self) -> None:
            self.cancel_calls = 0

        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            self.cancel_calls += 1

    fake_task = FakeTask()
    session.task = cast(asyncio.Task[None], cast(object, fake_task))

    with patch(
        "coding_agent.ui.session_manager.asyncio.wait_for",
        side_effect=asyncio.TimeoutError,
    ):
        with pytest.raises(RuntimeError, match="did not stop after cancellation"):
            await manager.shutdown_session_runtime(session_id)

    assert manager.has_session(session_id) is True
    assert fake_task.cancel_calls == 1


@pytest.mark.asyncio
async def test_register_session_closes_cached_runtime() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    close_calls: list[str] = []

    class FakeAdapter:
        async def run_turn(self, prompt: str) -> None:
            del prompt

        async def close(self) -> None:
            close_calls.append("closed")

    session.runtime_pipeline = object()
    session.runtime_ctx = object()
    session.runtime_adapter = FakeAdapter()

    manager.register_session(session)
    await asyncio.sleep(0)

    assert close_calls == ["closed"]
    assert session.runtime_pipeline is None
    assert session.runtime_ctx is None
    assert session.runtime_adapter is None


@pytest.mark.asyncio
async def test_run_agent_persists_tape_id_before_turn_completion() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt
            raise RuntimeError("turn exploded after tape allocation")

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=Tape(tape_id="allocated-before-run")
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        await manager.run_agent(session_id, "hello")

    persisted_payload = store.get(session_id)
    assert persisted_payload is not None
    assert persisted_payload["tape_id"] == "allocated-before-run"


@pytest.mark.asyncio
async def test_run_agent_rejects_concurrent_turn_for_same_session() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()
    started = asyncio.Event()
    release = asyncio.Event()

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt
            started.set()
            await release.wait()

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape") or Tape()
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)

        first = asyncio.create_task(manager.run_agent(session_id, "first"))
        await asyncio.wait_for(started.wait(), timeout=1)

        with pytest.raises(RuntimeError, match="turn already in progress"):
            await manager.run_agent(session_id, "second")

        release.set()
        await first


@pytest.mark.asyncio
async def test_rehydrated_session_rebuilds_runtime_from_persisted_tape() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()
    persisted_tape = Tape(tape_id="persisted-tape")
    persisted_tape.append(
        Entry(kind="message", payload={"role": "user", "content": "before restart"})
    )

    session = manager.get_session(session_id)
    session.tape_id = "persisted-tape"
    manager.register_session(session)

    rehydrated = SessionManager(store=store)
    created_tapes: list[Tape | None] = []

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        tape = kwargs.get("tape")
        created_tapes.append(tape)
        return fake_pipeline, types.SimpleNamespace(config={}, tape=tape)

    async def fake_restore_tape(tape_id: str):
        if tape_id == "persisted-tape":
            return persisted_tape
        return None

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(rehydrated, "_restore_tape", fake_restore_tape)

        await rehydrated.run_agent(session_id, "resume")

    assert created_tapes == [persisted_tape]


@pytest.mark.asyncio
async def test_session_store_persists_tape_id_for_cold_recovery() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt
            self.ctx.tape.tape_id = "persisted-stable-id"

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape") or Tape()
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        await manager.run_agent(session_id, "hello")

    reloaded = SessionManager(store=store).get_session(session_id)

    assert reloaded.tape_id == "persisted-stable-id"


@pytest.mark.asyncio
async def test_cold_restore_recovers_conversation_history() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.tape_id = "cold-tape"
    manager.register_session(session)

    restored_tape = Tape(tape_id="cold-tape")
    restored_tape.append(
        Entry(kind="message", payload={"role": "user", "content": "persisted history"})
    )
    restored_tape.append(
        Entry(
            kind="message", payload={"role": "assistant", "content": "persisted reply"}
        )
    )

    restored_entries: list[Entry] = []

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt
            restored_entries.extend(list(self.ctx.tape))

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(config={}, tape=kwargs.get("tape"))

    async def fake_restore_tape(tape_id: str):
        assert tape_id == "cold-tape"
        return restored_tape

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(manager, "_restore_tape", fake_restore_tape)
        await manager.run_agent(session_id, "resume")

    assert [entry.payload["content"] for entry in restored_entries[:2]] == [
        "persisted history",
        "persisted reply",
    ]


@pytest.mark.asyncio
async def test_cold_restore_does_not_restore_live_shell_state() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.tape_id = "cold-shell-tape"
    manager.register_session(session)

    restored_tape = Tape(tape_id="cold-shell-tape")
    observed_plugin_states: list[dict[str, object]] = []

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt
            observed_plugin_states.append(dict(self.ctx.plugin_states))

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape"), plugin_states={}
        )

    async def fake_restore_tape(tape_id: str):
        assert tape_id == "cold-shell-tape"
        return restored_tape

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(manager, "_restore_tape", fake_restore_tape)
        await manager.run_agent(session_id, "resume")

    assert observed_plugin_states == [{}]


@pytest.mark.asyncio
async def test_restore_truncates_tape_store_to_checkpoint_entry_count() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.tape_id = "checkpoint-tape"
    manager.register_session(session)

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-1",
            tape_id="checkpoint-tape",
            entry_count=2,
            window_start=0,
        ),
        tape_entries=(
            {
                "id": "e1",
                "kind": "message",
                "payload": {"role": "user", "content": "a"},
                "timestamp": 1.0,
            },
            {
                "id": "e2",
                "kind": "message",
                "payload": {"role": "assistant", "content": "b"},
                "timestamp": 2.0,
            },
        ),
        plugin_states={},
        extra={},
    )

    truncate_calls: list[tuple[str, int]] = []

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            truncate_calls.append((tape_id, keep))

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-1"
            return snapshot

        async def list(self, tape_id: str):
            assert tape_id == "checkpoint-tape"
            return [snapshot.meta]

        async def delete(self, checkpoint_id: str) -> None:
            raise AssertionError("no future checkpoints to delete")

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            return None

        async def run_turn(self, prompt: str) -> None:
            del prompt

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={},
            tape=kwargs.get("tape"),
            plugin_states={},
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        await manager._restore_checkpoint(session, "cp-1")

    assert truncate_calls == [("checkpoint-tape", 2)]


@pytest.mark.asyncio
async def test_restore_rejects_checkpoint_with_mismatched_entry_count() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.tape_id = "checkpoint-tape"
    manager.register_session(session)

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-bad",
            tape_id="checkpoint-tape",
            entry_count=2,
            window_start=0,
        ),
        tape_entries=(
            {
                "id": "e1",
                "kind": "message",
                "payload": {"role": "user", "content": "a"},
                "timestamp": 1.0,
            },
        ),
        plugin_states={},
        extra={},
    )

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-bad"
            return snapshot

        async def list(self, tape_id: str):
            raise AssertionError("should not list checkpoints for invalid snapshot")

        async def delete(self, checkpoint_id: str) -> None:
            raise AssertionError("should not delete checkpoints for invalid snapshot")

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            raise AssertionError("should not truncate invalid checkpoint snapshot")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        mp.setattr(
            "coding_agent.__main__.create_agent",
            lambda **kwargs: (_ for _ in ()).throw(
                AssertionError("should not build runtime for invalid checkpoint")
            ),
        )

        with pytest.raises(ValueError, match="entry_count"):
            await manager._restore_checkpoint(session, "cp-bad")


@pytest.mark.asyncio
async def test_restore_injects_checkpoint_plugin_states_before_mount() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.tape_id = "checkpoint-plugin-tape"
    manager.register_session(session)

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-plugin",
            tape_id="checkpoint-plugin-tape",
            entry_count=0,
            window_start=0,
        ),
        tape_entries=(),
        plugin_states={"topic": {"current_topic_id": "topic-1"}},
        extra={},
    )

    observed_before_mount: list[dict[str, object]] = []

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-plugin"
            return snapshot

        async def list(self, tape_id: str):
            return [snapshot.meta]

        async def delete(self, checkpoint_id: str) -> None:
            return None

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            return None

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            observed_before_mount.append(dict(self.ctx.plugin_states))

        async def run_turn(self, prompt: str) -> None:
            del prompt

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={},
            tape=kwargs.get("tape"),
            plugin_states={},
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        await manager._restore_checkpoint(session, "cp-plugin")

    assert observed_before_mount == [{"topic": {"current_topic_id": "topic-1"}}]


@pytest.mark.asyncio
async def test_restore_rewinds_restart_safe_agent_configuration_from_checkpoint_extra() -> (
    None
):
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session(
        provider_name="current-provider",
        model_name="current-model",
        base_url="http://current.local",
        max_steps=99,
        approval_policy=ApprovalPolicy.AUTO,
    )
    session = manager.get_session(session_id)
    session.tape_id = "checkpoint-config-tape"
    manager.register_session(session)

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-config",
            tape_id="checkpoint-config-tape",
            entry_count=2,
            window_start=0,
        ),
        tape_entries=(
            {
                "id": "e1",
                "kind": "message",
                "payload": {"role": "user", "content": "before config drift"},
                "timestamp": 1.0,
            },
            {
                "id": "e2",
                "kind": "message",
                "payload": {"role": "assistant", "content": "checkpoint saved"},
                "timestamp": 2.0,
            },
        ),
        plugin_states={},
        extra={
            "session_restart_config": {
                "provider_name": "checkpoint-provider",
                "model_name": "checkpoint-model",
                "base_url": "http://checkpoint.local",
                "max_steps": 7,
                "approval_policy": "interactive",
            }
        },
    )

    truncate_calls: list[tuple[str, int]] = []
    captured_kwargs: dict[str, object] = {}

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-config"
            return snapshot

        async def list(self, tape_id: str):
            assert tape_id == "checkpoint-config-tape"
            return [snapshot.meta]

        async def delete(self, checkpoint_id: str) -> None:
            raise AssertionError("no future checkpoints to delete")

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            truncate_calls.append((tape_id, keep))

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            return None

        async def run_turn(self, prompt: str) -> None:
            del prompt

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_pipeline, types.SimpleNamespace(
            config={},
            tape=kwargs.get("tape"),
            plugin_states={},
        )

    session.provider_name = "mutated-provider"
    session.model_name = "mutated-model"
    session.base_url = "http://mutated.local"
    session.max_steps = 42
    session.approval_policy = ApprovalPolicy.YOLO

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        await manager._restore_checkpoint(session, "cp-config")

    assert truncate_calls == [("checkpoint-config-tape", 2)]
    assert captured_kwargs["provider_override"] == "checkpoint-provider"
    assert captured_kwargs["model_override"] == "checkpoint-model"
    assert captured_kwargs["base_url_override"] == "http://checkpoint.local"
    assert captured_kwargs["max_steps_override"] == 7
    assert captured_kwargs["approval_mode_override"] == "interactive"
    assert session.provider_name == "checkpoint-provider"
    assert session.model_name == "checkpoint-model"
    assert session.base_url == "http://checkpoint.local"
    assert session.max_steps == 7
    assert session.approval_policy is ApprovalPolicy.INTERACTIVE


@pytest.mark.asyncio
async def test_run_agent_uses_resolved_workspace_root_from_binding() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.repo_path = Path("/tmp/not-used-directly")
    session.execution_binding = LocalExecutionBinding(
        workspace_root="/tmp/bound-workspace"
    )
    manager.register_session(session)

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )
    fake_ctx = types.SimpleNamespace(config={}, tape=Tape())
    captured_kwargs: dict[str, object] = {}

    def fake_create_agent(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_pipeline, fake_ctx

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        await manager.run_agent(session_id, "hello")

    assert captured_kwargs["workspace_root"] == Path("/tmp/bound-workspace").resolve()


@pytest.mark.asyncio
async def test_restore_checkpoint_preserves_execution_binding() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.tape_id = "binding-restore-tape"
    session.execution_binding = LocalExecutionBinding(
        workspace_root="/tmp/restore-bound"
    )
    manager.register_session(session)

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-binding",
            tape_id="binding-restore-tape",
            entry_count=0,
            window_start=0,
        ),
        tape_entries=(),
        plugin_states={},
        extra={},
    )
    captured_kwargs: dict[str, object] = {}

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-binding"
            return snapshot

        async def list(self, tape_id: str):
            assert tape_id == "binding-restore-tape"
            return [snapshot.meta]

        async def delete(self, checkpoint_id: str) -> None:
            return None

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            return None

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            return None

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape"), plugin_states={}
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        await manager._restore_checkpoint(session, "cp-binding")

    assert captured_kwargs["workspace_root"] == Path("/tmp/restore-bound").resolve()
    assert isinstance(session.execution_binding, LocalExecutionBinding)
    assert session.execution_binding.workspace_root == "/tmp/restore-bound"


@pytest.mark.asyncio
async def test_restore_legacy_checkpoint_without_session_config_uses_current_session_metadata() -> (
    None
):
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session(
        provider_name="current-provider",
        model_name="current-model",
        base_url="http://current.local",
        max_steps=11,
        approval_policy=ApprovalPolicy.AUTO,
    )
    session = manager.get_session(session_id)
    session.tape_id = "legacy-checkpoint-tape"
    manager.register_session(session)

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-legacy",
            tape_id="legacy-checkpoint-tape",
            entry_count=0,
            window_start=0,
        ),
        tape_entries=(),
        plugin_states={},
        extra={},
    )

    captured_kwargs: dict[str, object] = {}

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-legacy"
            return snapshot

        async def list(self, tape_id: str):
            assert tape_id == "legacy-checkpoint-tape"
            return [snapshot.meta]

        async def delete(self, checkpoint_id: str) -> None:
            return None

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            return None

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            return None

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape"), plugin_states={}
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        await manager._restore_checkpoint(session, "cp-legacy")

    assert captured_kwargs["provider_override"] == "current-provider"
    assert captured_kwargs["model_override"] == "current-model"
    assert captured_kwargs["base_url_override"] == "http://current.local"
    assert captured_kwargs["max_steps_override"] == 11
    assert captured_kwargs["approval_mode_override"] == "auto"


@pytest.mark.asyncio
async def test_restore_rejects_partial_checkpoint_session_config_payload() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session(
        provider_name="current-provider",
        model_name="current-model",
        base_url="http://current.local",
        max_steps=11,
        approval_policy=ApprovalPolicy.AUTO,
    )
    session = manager.get_session(session_id)
    session.tape_id = "invalid-checkpoint-tape"
    manager.register_session(session)

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-invalid",
            tape_id="invalid-checkpoint-tape",
            entry_count=0,
            window_start=0,
        ),
        tape_entries=(),
        plugin_states={},
        extra={
            "session_restart_config": {
                "provider_name": "checkpoint-provider",
                "approval_policy": "interactive",
            }
        },
    )

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-invalid"
            return snapshot

        async def list(self, tape_id: str):
            raise AssertionError("invalid checkpoint config should fail early")

        async def delete(self, checkpoint_id: str) -> None:
            raise AssertionError("invalid checkpoint config should fail early")

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            raise AssertionError("invalid checkpoint config should not truncate")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        mp.setattr(
            "coding_agent.__main__.create_agent",
            lambda **kwargs: (_ for _ in ()).throw(
                AssertionError("invalid checkpoint config should not build runtime")
            ),
        )

        with pytest.raises(TypeError, match="missing .*model_name"):
            await manager._restore_checkpoint(session, "cp-invalid")


@pytest.mark.asyncio
async def test_restore_clears_hot_provider_override_when_checkpoint_rewinds_provider_metadata() -> (
    None
):
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    current_provider = MockProvider()
    session_id = await manager.create_session(
        provider=current_provider,
        provider_name="current-provider",
        model_name="current-model",
        base_url="http://current.local",
        max_steps=13,
        approval_policy=ApprovalPolicy.AUTO,
    )
    session = manager.get_session(session_id)
    session.tape_id = "hot-provider-checkpoint-tape"
    manager.register_session(session)

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-hot-provider",
            tape_id="hot-provider-checkpoint-tape",
            entry_count=0,
            window_start=0,
        ),
        tape_entries=(),
        plugin_states={},
        extra={
            "session_restart_config": {
                "provider_name": "checkpoint-provider",
                "model_name": "checkpoint-model",
                "base_url": "http://checkpoint.local",
                "max_steps": 5,
                "approval_policy": "interactive",
            }
        },
    )

    llm_plugin = types.SimpleNamespace(_instance=None)

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-hot-provider"
            return snapshot

        async def list(self, tape_id: str):
            assert tape_id == "hot-provider-checkpoint-tape"
            return [snapshot.meta]

        async def delete(self, checkpoint_id: str) -> None:
            return None

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            return None

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            return None

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(get=lambda _: llm_plugin)
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape"), plugin_states={}
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        await manager._restore_checkpoint(session, "cp-hot-provider")

    assert llm_plugin._instance is None
    assert session.provider is None


@pytest.mark.asyncio
async def test_restore_does_not_reuse_hot_provider_when_model_changes_with_same_provider() -> (
    None
):
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    hot_provider = MockProvider()
    hot_provider._model_name = "current-model"

    session_id = await manager.create_session(
        provider=hot_provider,
        provider_name="openai",
        model_name="current-model",
        base_url="http://current.local",
        max_steps=13,
        approval_policy=ApprovalPolicy.AUTO,
    )
    session = manager.get_session(session_id)
    session.tape_id = "same-provider-different-model-tape"
    manager.register_session(session)

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-same-provider-new-model",
            tape_id="same-provider-different-model-tape",
            entry_count=0,
            window_start=0,
        ),
        tape_entries=(),
        plugin_states={},
        extra={
            "session_restart_config": {
                "provider_name": "openai",
                "model_name": "rewound-model",
                "base_url": "http://current.local",
                "max_steps": 5,
                "approval_policy": "interactive",
            }
        },
    )

    llm_plugin = types.SimpleNamespace(_instance=None)

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-same-provider-new-model"
            return snapshot

        async def list(self, tape_id: str):
            assert tape_id == "same-provider-different-model-tape"
            return [snapshot.meta]

        async def delete(self, checkpoint_id: str) -> None:
            return None

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            return None

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            return None

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(get=lambda _: llm_plugin)
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape"), plugin_states={}
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        await manager._restore_checkpoint(session, "cp-same-provider-new-model")

    assert llm_plugin._instance is None
    assert session.provider is None


@pytest.mark.asyncio
async def test_restore_closes_existing_runtime_before_replacing_it() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.tape_id = "checkpoint-plugin-tape"
    manager.register_session(session)

    close_calls: list[str] = []

    class ExistingAdapter:
        async def run_turn(self, prompt: str) -> None:
            del prompt

        async def close(self) -> None:
            close_calls.append("old-runtime")

    session.runtime_pipeline = object()
    session.runtime_ctx = object()
    session.runtime_adapter = ExistingAdapter()

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-plugin",
            tape_id="checkpoint-plugin-tape",
            entry_count=0,
            window_start=0,
        ),
        tape_entries=(),
        plugin_states={},
        extra={},
    )

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-plugin"
            return snapshot

        async def list(self, tape_id: str):
            return [snapshot.meta]

        async def delete(self, checkpoint_id: str) -> None:
            return None

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            return None

    class NewAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            return None

        async def close(self) -> None:
            close_calls.append("new-runtime")

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={},
            tape=kwargs.get("tape"),
            plugin_states={},
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", NewAdapter)
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        await manager._restore_checkpoint(session, "cp-plugin")

    assert close_calls == ["old-runtime"]
    assert isinstance(session.runtime_adapter, NewAdapter)


def test_clear_sessions_clears_session_turn_locks() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    _ = manager._turn_lock_for("session-a")
    _ = manager._turn_lock_for("session-b")

    assert manager._session_turn_locks

    manager.clear_sessions()

    assert manager._session_turn_locks == {}


@pytest.mark.asyncio
async def test_clear_sessions_closes_cached_runtimes() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    first = manager.get_session(await manager.create_session())
    second = manager.get_session(await manager.create_session())
    close_calls: list[str] = []

    class FakeAdapter:
        def __init__(self, name: str) -> None:
            self.name = name

        async def run_turn(self, prompt: str) -> None:
            del prompt

        async def close(self) -> None:
            close_calls.append(self.name)

    first.runtime_pipeline = object()
    first.runtime_ctx = object()
    first.runtime_adapter = FakeAdapter("first")
    second.runtime_pipeline = object()
    second.runtime_ctx = object()
    second.runtime_adapter = FakeAdapter("second")

    manager.clear_sessions()
    await asyncio.sleep(0)

    assert close_calls == ["first", "second"]
