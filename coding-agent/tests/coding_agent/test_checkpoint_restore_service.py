from __future__ import annotations

import types
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from agentkit.checkpoint.models import CheckpointMeta, CheckpointSnapshot
from coding_agent.approval import ApprovalPolicy
from coding_agent.runs import (
    CheckpointRestoreService,
    CheckpointRestoredRuntime,
)


@dataclass
class FakeRestoreSession:
    id: str = "session-1"
    tape_id: str | None = "tape-1"
    provider_name: str | None = "provider-old"
    model_name: str | None = "model-old"
    base_url: str | None = "http://old.local"
    max_steps: int = 5
    approval_policy: ApprovalPolicy = ApprovalPolicy.AUTO
    provider: object | None = object()
    runtime_pipeline: object | None = None
    runtime_ctx: object | None = None
    runtime_adapter: object | None = None

    def attach_runtime_binding(
        self,
        *,
        pipeline: object,
        ctx: object,
        adapter: object,
    ) -> None:
        self.runtime_pipeline = pipeline
        self.runtime_ctx = ctx
        self.runtime_adapter = adapter


def _entry(content: str) -> dict[str, Any]:
    return {
        "id": f"entry-{content}",
        "kind": "message",
        "payload": {"role": "user", "content": content},
        "timestamp": 1.0,
    }


def _meta(
    checkpoint_id: str,
    *,
    tape_id: str = "tape-1",
    entry_count: int = 1,
    window_start: int = 0,
) -> CheckpointMeta:
    return CheckpointMeta(
        checkpoint_id=checkpoint_id,
        tape_id=tape_id,
        session_id="session-1",
        entry_count=entry_count,
        window_start=window_start,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        label=None,
    )


@pytest.mark.asyncio
async def test_checkpoint_restore_service_rejects_mismatched_entry_count_before_side_effects() -> (
    None
):
    session = FakeRestoreSession()
    snapshot = CheckpointSnapshot(
        meta=_meta("cp-bad", entry_count=2),
        tape_entries=(_entry("one"),),
        plugin_states={},
        extra={},
    )

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str) -> CheckpointSnapshot:
            assert checkpoint_id == "cp-bad"
            return snapshot

        async def list(self, tape_id: str) -> list[CheckpointMeta]:
            raise AssertionError("invalid snapshot should not list checkpoints")

        async def delete(self, checkpoint_id: str) -> None:
            raise AssertionError("invalid snapshot should not delete checkpoints")

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            raise AssertionError("invalid snapshot should not truncate tape")

    async def prepare_runtime(**kwargs: object) -> CheckpointRestoredRuntime:
        raise AssertionError(f"invalid snapshot should not prepare runtime: {kwargs!r}")

    async def close_runtime(restore_session: FakeRestoreSession) -> None:
        raise AssertionError(f"invalid snapshot should not close runtime: {restore_session!r}")

    async def persist_session(restore_session: FakeRestoreSession) -> None:
        raise AssertionError(f"invalid snapshot should not persist session: {restore_session!r}")

    service = CheckpointRestoreService(
        checkpoint_service=FakeCheckpointService(),
        tape_store=FakeTapeStore(),
        prepare_runtime=prepare_runtime,
        close_runtime=close_runtime,
        persist_session=persist_session,
    )

    with pytest.raises(ValueError, match="entry_count"):
        await service.restore(session, "cp-bad")


@pytest.mark.asyncio
async def test_checkpoint_restore_service_rewinds_session_and_prunes_future_checkpoints() -> (
    None
):
    session = FakeRestoreSession()
    pipeline = object()
    adapter = object()
    snapshot = CheckpointSnapshot(
        meta=_meta("cp-restore", entry_count=1),
        tape_entries=(_entry("one"),),
        plugin_states={"plugin": {"state": "checkpoint"}},
        extra={
            "session_restart_config": {
                "provider_name": "provider-new",
                "model_name": "model-new",
                "base_url": "http://new.local",
                "max_steps": 11,
                "approval_policy": "interactive",
            }
        },
    )
    future = _meta("cp-future", entry_count=2)
    current = _meta("cp-restore", entry_count=1)
    truncate_calls: list[tuple[str, int]] = []
    deleted: list[str] = []
    closed: list[str] = []
    persisted: list[str] = []
    prepared: dict[str, object] = {}

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str) -> CheckpointSnapshot:
            assert checkpoint_id == "cp-restore"
            return snapshot

        async def list(self, tape_id: str) -> list[CheckpointMeta]:
            assert tape_id == "tape-1"
            return [current, future]

        async def delete(self, checkpoint_id: str) -> None:
            deleted.append(checkpoint_id)

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            truncate_calls.append((tape_id, keep))

    async def prepare_runtime(**kwargs: object) -> CheckpointRestoredRuntime:
        prepared.update(kwargs)
        restored_tape = kwargs["restored_tape"]
        return CheckpointRestoredRuntime(
            pipeline=pipeline,
            ctx=types.SimpleNamespace(tape=restored_tape),
            adapter=adapter,
        )

    async def close_runtime(restore_session: FakeRestoreSession) -> None:
        closed.append(restore_session.id)

    async def persist_session(restore_session: FakeRestoreSession) -> None:
        persisted.append(restore_session.id)

    service = CheckpointRestoreService(
        checkpoint_service=FakeCheckpointService(),
        tape_store=FakeTapeStore(),
        prepare_runtime=prepare_runtime,
        close_runtime=close_runtime,
        persist_session=persist_session,
    )

    await service.restore(session, "cp-restore")

    assert prepared["session"] is session
    assert len(prepared["restored_tape"]) == 1
    assert prepared["plugin_states"] == {"plugin": {"state": "checkpoint"}}
    assert truncate_calls == [("tape-1", 1)]
    assert closed == ["session-1"]
    assert persisted == ["session-1"]
    assert deleted == ["cp-future"]
    assert session.tape_id == "tape-1"
    assert session.provider_name == "provider-new"
    assert session.model_name == "model-new"
    assert session.base_url == "http://new.local"
    assert session.max_steps == 11
    assert session.approval_policy is ApprovalPolicy.INTERACTIVE
    assert session.provider is None
    assert session.runtime_pipeline is pipeline
    assert session.runtime_ctx.tape is prepared["restored_tape"]
    assert session.runtime_adapter is adapter
