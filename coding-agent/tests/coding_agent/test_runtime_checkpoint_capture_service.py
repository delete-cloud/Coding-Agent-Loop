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
    runtime_version: str = "legacy"
    current_turn_id: str | None = None

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
        self.restore_point_calls: list[dict[str, Any]] = []

    async def capture(
        self,
        ctx: object,
        *,
        label: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> CheckpointMeta:
        self.calls.append((ctx, label, extra))
        return self.checkpoint

    async def capture_restore_point(
        self,
        *,
        tape_id: str,
        session_id: str,
        label: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> CheckpointMeta:
        self.restore_point_calls.append(
            {
                "tape_id": tape_id,
                "session_id": session_id,
                "label": label,
                "extra": extra,
            }
        )
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


@pytest.mark.asyncio
async def test_new_runtime_capture_uses_restore_point_backend() -> None:
    from coding_agent.runtime_activation import (
        CHECKPOINT_FORMAT_KEY,
        OPERATION_STATE_VERSION_KEY,
        RUNTIME_VERSION_NEW,
    )

    session = FakeSession(runtime_version=RUNTIME_VERSION_NEW)
    ctx = types.SimpleNamespace(tape=types.SimpleNamespace(tape_id="stable-tape"))
    backend = RecordingCheckpointBackend(_checkpoint())

    async def ensure_runtime(session_id: str):
        del session_id
        return ctx

    async def persist_session(current_session: FakeSession) -> None:
        del current_session

    checkpoint = await RuntimeCheckpointCaptureService(
        checkpoint_service=lambda: backend,
        ensure_runtime=ensure_runtime,
        persist_session=persist_session,
    ).capture(session, label="g1")

    assert checkpoint == backend.checkpoint
    assert backend.calls == []
    assert backend.restore_point_calls == [
        {
            "tape_id": "stable-tape",
            "session_id": "session-1",
            "label": "g1",
            "extra": {
                CHECKPOINT_FORMAT_KEY: RUNTIME_VERSION_NEW,
                OPERATION_STATE_VERSION_KEY: None,
                "session_restart_config": {
                    "provider_name": "anthropic",
                    "model_name": "claude-checkpoint",
                    "base_url": "http://checkpoint.local",
                    "max_steps": 17,
                    "approval_policy": "interactive",
                },
            },
        }
    ]


@pytest.mark.asyncio
async def test_new_runtime_capture_stamps_operation_state_version() -> None:
    from agentkit.runtime.contracts import CommitRef, OperationStateVersion
    from coding_agent.runtime_activation import (
        CHECKPOINT_FORMAT_KEY,
        OPERATION_STATE_VERSION_KEY,
        RUNTIME_VERSION_NEW,
    )

    state = OperationStateVersion(
        run_id="run-1",
        revision=3,
        projection_epoch=1,
        commit_ref=CommitRef(
            transition_id="transition-1",
            fact_seq_start="1",
            fact_seq_end="2",
        ),
        value={"phase": "ready"},
    )
    session = FakeSession(
        runtime_version=RUNTIME_VERSION_NEW,
        current_turn_id="run-1",
    )
    ctx = types.SimpleNamespace(tape=types.SimpleNamespace(tape_id="stable-tape"))
    backend = RecordingCheckpointBackend(_checkpoint())

    async def ensure_runtime(session_id: str):
        del session_id
        return ctx

    async def persist_session(current_session: FakeSession) -> None:
        del current_session

    async def load_operation_state(
        session_id: str, run_id: str
    ) -> OperationStateVersion:
        assert session_id == "session-1"
        assert run_id == "run-1"
        return state

    await RuntimeCheckpointCaptureService(
        checkpoint_service=lambda: backend,
        ensure_runtime=ensure_runtime,
        persist_session=persist_session,
        load_operation_state=load_operation_state,
    ).capture(session, label="g3")

    extra = backend.restore_point_calls[0]["extra"]
    assert extra[CHECKPOINT_FORMAT_KEY] == RUNTIME_VERSION_NEW
    assert extra[OPERATION_STATE_VERSION_KEY] == {
        "run_id": "run-1",
        "revision": 3,
        "projection_epoch": 1,
        "commit_ref": {
            "transition_id": "transition-1",
            "fact_seq_start": "1",
            "fact_seq_end": "2",
        },
        "value": {"phase": "ready"},
    }


def test_serialize_operation_state_version_plain_json_nested_mapping() -> None:
    import json

    from agentkit.runtime.contracts import CommitRef, OperationStateVersion
    from coding_agent.runs.checkpoint_capture import serialize_operation_state_version

    state = OperationStateVersion(
        run_id="run-1",
        revision=1,
        projection_epoch=0,
        commit_ref=CommitRef(transition_id="t1", fact_seq_start="1", fact_seq_end="1"),
        value={"nested": {"ok": True}},
    )
    payload = serialize_operation_state_version(state)
    assert payload is not None
    json.dumps(payload)
    assert payload["value"] == {"nested": {"ok": True}}
