from __future__ import annotations

import types

import pytest

from coding_agent.wire.protocol import (
    ApprovalRequest,
    CompletionStatus,
    StreamDelta,
    ToolCallDelta,
    TurnEnd,
)
from coding_agent.ui.session_manager import MockProvider, SessionManager


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
