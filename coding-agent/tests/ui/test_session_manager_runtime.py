from __future__ import annotations

import types

import pytest

from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from coding_agent.wire.protocol import (
    ApprovalRequest,
    CompletionStatus,
    StreamDelta,
    ToolCallDelta,
    TurnEnd,
)
from coding_agent.ui.session_manager import MockProvider, SessionManager
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
    fake_ctx = types.SimpleNamespace(config={})

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
    fake_ctx = types.SimpleNamespace(config={})

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
    assert recorded_tapes[1] is not None
    assert recorded_tapes[1].tape_id == "stable-session-tape"
    persisted_payload = store.get(session_id)
    assert persisted_payload is not None
    assert persisted_payload["tape_id"] == "stable-session-tape"


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
