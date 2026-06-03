from __future__ import annotations

import types
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from agentkit.checkpoint.models import CheckpointMeta

from coding_agent.approval import ApprovalPolicy
from coding_agent.runs import RuntimeCheckpointCaptureService


@dataclass
class FakeSession:
    id: str = "session-1"
    tape_id: str | None = None
    provider_name: str | None = "anthropic"
    model_name: str | None = "claude-checkpoint"
    base_url: str | None = "http://checkpoint.local"
    max_steps: int = 17
    approval_policy: ApprovalPolicy = ApprovalPolicy.INTERACTIVE
    provider: object | None = None

    def attach_runtime_binding(
        self,
        *,
        pipeline: object,
        ctx: object,
        adapter: object,
    ) -> None:
        del pipeline, ctx, adapter


class RecordingCheckpointBackend:
    def __init__(self, checkpoint: CheckpointMeta) -> None:
        self.checkpoint = checkpoint
        self.calls: list[tuple[object, str | None, dict[str, Any] | None]] = []

    async def capture(
        self,
        ctx: object,
        *,
        label: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> CheckpointMeta:
        self.calls.append((ctx, label, extra))
        return self.checkpoint


def _checkpoint(session_id: str = "session-1") -> CheckpointMeta:
    return CheckpointMeta(
        checkpoint_id="cp-save",
        tape_id="stable-tape",
        session_id=session_id,
        entry_count=0,
        window_start=0,
        created_at=datetime.now(),
        label="manual save",
    )


@pytest.mark.asyncio
async def test_capture_stamps_session_config_and_persists_tape_id() -> None:
    session = FakeSession()
    ctx = types.SimpleNamespace(tape=types.SimpleNamespace(tape_id="stable-tape"))
    backend = RecordingCheckpointBackend(_checkpoint())
    ensured: list[str] = []
    persisted: list[str] = []

    async def ensure_runtime(session_id: str):
        ensured.append(session_id)
        return ctx

    async def persist_session(current_session: FakeSession) -> None:
        persisted.append(current_session.id)

    checkpoint = await RuntimeCheckpointCaptureService(
        checkpoint_service=lambda: backend,
        ensure_runtime=ensure_runtime,
        persist_session=persist_session,
    ).capture(session, label="manual save", extra={"workspace": "/tmp/repo"})

    assert checkpoint == backend.checkpoint
    assert ensured == ["session-1"]
    assert persisted == ["session-1"]
    assert session.tape_id == "stable-tape"
    assert backend.calls == [
        (
            ctx,
            "manual save",
            {
                "workspace": "/tmp/repo",
                "session_restart_config": {
                    "provider_name": "anthropic",
                    "model_name": "claude-checkpoint",
                    "base_url": "http://checkpoint.local",
                    "max_steps": 17,
                    "approval_policy": "interactive",
                },
            },
        )
    ]


@pytest.mark.asyncio
async def test_capture_rejects_reserved_session_config_key_before_backend() -> None:
    session = FakeSession()
    ctx = types.SimpleNamespace(tape=types.SimpleNamespace(tape_id="stable-tape"))
    backend = RecordingCheckpointBackend(_checkpoint())
    persisted: list[str] = []

    async def ensure_runtime(session_id: str):
        del session_id
        return ctx

    async def persist_session(current_session: FakeSession) -> None:
        persisted.append(current_session.id)

    with pytest.raises(ValueError, match="reserved checkpoint metadata key"):
        await RuntimeCheckpointCaptureService(
            checkpoint_service=lambda: backend,
            ensure_runtime=ensure_runtime,
            persist_session=persist_session,
        ).capture(
            session,
            extra={"session_restart_config": {"provider_name": "oops"}},
        )

    assert backend.calls == []
    assert persisted == []


@pytest.mark.asyncio
async def test_capture_reads_checkpoint_backend_provider_at_call_time() -> None:
    session = FakeSession()
    ctx = types.SimpleNamespace(tape=types.SimpleNamespace(tape_id="stable-tape"))
    backend_a = RecordingCheckpointBackend(_checkpoint())
    backend_b = RecordingCheckpointBackend(
        CheckpointMeta(
            checkpoint_id="cp-later",
            tape_id="stable-tape",
            session_id="session-1",
            entry_count=0,
            window_start=0,
            created_at=datetime.now(),
            label=None,
        )
    )
    current_backend = backend_a

    async def ensure_runtime(session_id: str):
        del session_id
        return ctx

    async def persist_session(current_session: FakeSession) -> None:
        del current_session

    service = RuntimeCheckpointCaptureService(
        checkpoint_service=lambda: current_backend,
        ensure_runtime=ensure_runtime,
        persist_session=persist_session,
    )
    current_backend = backend_b

    checkpoint = await service.capture(session)

    assert checkpoint.checkpoint_id == "cp-later"
    assert backend_a.calls == []
    assert len(backend_b.calls) == 1
