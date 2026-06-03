from __future__ import annotations

import types
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agentkit.checkpoint.models import CheckpointMeta, CheckpointSnapshot
from coding_agent.approval import ApprovalPolicy
from coding_agent.executors import (
    LocalDaemonExecutor,
    LocalDaemonRuntimeBinding,
    LocalDaemonRuntimePreparation,
)
from coding_agent.runs import (
    IsolationPolicy,
    LocalDaemonExecutorRef,
    LocalPathWorkspaceRef,
    RunRequest,
    RunTarget,
)
from coding_agent.runs.runtime_checkpoint_restore import RuntimeCheckpointRestoreService
from coding_agent.runs.runtime_checkpoint_restore import (
    RuntimeCheckpointRestoreOrchestrationService,
)


@dataclass
class FakeSession:
    id: str = "session-1"
    tape_id: str | None = "tape-1"
    provider_name: str | None = "provider-a"
    model_name: str | None = "model-a"
    base_url: str | None = "http://provider.local"
    max_steps: int = 5
    approval_policy: ApprovalPolicy = ApprovalPolicy.AUTO
    provider: object | None = None
    wire: object = "wire"
    default_run_target: RunTarget | None = None
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


class FakeAdapter:
    def __init__(self, pipeline: object, ctx: object, consumer: object) -> None:
        del pipeline, consumer
        self.ctx = ctx
        self.plugin_states_at_initialize: dict[str, object] | None = None

    async def initialize(self) -> None:
        self.plugin_states_at_initialize = dict(self.ctx.plugin_states)


class RecordingExecutor(LocalDaemonExecutor):
    def __init__(self) -> None:
        self.preparations: list[LocalDaemonRuntimePreparation] = []

    async def prepare_runtime(
        self,
        preparation: LocalDaemonRuntimePreparation,
    ) -> LocalDaemonRuntimeBinding:
        self._validate_request_target(preparation.request)
        self.preparations.append(preparation)
        return await preparation.runtime_provider.prepare_runtime(preparation.request)


class RecordingRestoreAdmission:
    def __init__(self, session: object) -> None:
        self.session = session
        self.session_ids: list[str] = []

    async def run_exclusive(self, session_id: str, body):
        self.session_ids.append(session_id)
        await body(self.session)


def _target() -> RunTarget:
    return RunTarget(
        workspace=LocalPathWorkspaceRef(path="/repo"),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="default_local_sandbox"),
    )


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
    entry_count: int,
) -> CheckpointMeta:
    return CheckpointMeta(
        checkpoint_id=checkpoint_id,
        tape_id="tape-1",
        session_id="session-1",
        entry_count=entry_count,
        window_start=0,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        label=None,
    )


@pytest.mark.asyncio
async def test_runtime_checkpoint_restore_service_composes_restore_runtime() -> None:
    session = FakeSession(default_run_target=_target())
    snapshot = CheckpointSnapshot(
        meta=_meta("cp-restore", entry_count=1),
        tape_entries=(_entry("one"),),
        plugin_states={"topic": {"current_topic_id": "topic-1"}},
        extra={
            "session_restart_config": {
                "provider_name": "provider-a",
                "model_name": "model-a",
                "base_url": "http://provider.local",
                "max_steps": 8,
                "approval_policy": "interactive",
            }
        },
    )
    current = _meta("cp-restore", entry_count=1)
    future = _meta("cp-future", entry_count=2)
    executor = RecordingExecutor()
    created: dict[str, object] = {}
    truncate_calls: list[tuple[str, int]] = []
    deleted: list[str] = []
    closed: list[str] = []
    persisted: list[tuple[str, int]] = []

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

    def create_agent(**kwargs: object) -> tuple[object, object]:
        created.update(kwargs)
        llm_plugin = types.SimpleNamespace(_instance=None)
        pipeline = types.SimpleNamespace(
            _registry=types.SimpleNamespace(get=lambda name: llm_plugin)
        )
        ctx = types.SimpleNamespace(
            config={},
            plugin_states={},
            tape=kwargs["tape"],
            bound_subagent_publisher=False,
        )
        return pipeline, ctx

    async def close_runtime(restore_session: FakeSession) -> None:
        closed.append(restore_session.id)

    async def persist_session(restore_session: FakeSession) -> None:
        persisted.append((restore_session.id, restore_session.max_steps))

    await RuntimeCheckpointRestoreService(
        checkpoint_service=lambda: FakeCheckpointService(),
        tape_store=lambda: FakeTapeStore(),
        local_daemon_executor=executor,
        resolve_environment_for_run_target=lambda target: types.SimpleNamespace(
            target=target
        ),
        workspace_root_for_environment=lambda environment: Path("/repo"),
        create_agent_for_session=create_agent,
        bind_subagent_message_publisher=lambda ctx: setattr(
            ctx,
            "bound_subagent_publisher",
            True,
        ),
        restore_consumer_factory=lambda wire: f"consumer:{wire}",
        adapter_factory=lambda pipeline, ctx, consumer: FakeAdapter(
            pipeline,
            ctx,
            consumer,
        ),
        runtime_preparation_request=lambda runtime_session, *, purpose: RunRequest(
            session_id=runtime_session.id,
            run_id="runtime-prepare",
            target=runtime_session.default_run_target,
            metadata={"purpose": purpose},
        ),
        close_runtime=close_runtime,
        persist_session=persist_session,
    ).restore(session, "cp-restore")

    assert len(executor.preparations) == 1
    assert executor.preparations[0].request.target == session.default_run_target
    assert executor.preparations[0].request.metadata == {
        "purpose": "checkpoint_restore"
    }
    assert created["workspace_root"] == Path("/repo")
    assert created["model_override"] == "model-a"
    assert created["max_steps_override"] == 8
    assert created["approval_mode_override"] == "interactive"
    assert created["session_id_override"] == "session-1"
    assert session.runtime_ctx.config["wire_consumer"] == "consumer:wire"
    assert session.runtime_ctx.bound_subagent_publisher is True
    assert session.runtime_ctx.plugin_states == {
        "topic": {"current_topic_id": "topic-1"}
    }
    assert session.runtime_adapter.plugin_states_at_initialize == {
        "topic": {"current_topic_id": "topic-1"}
    }
    assert truncate_calls == [("tape-1", 1)]
    assert closed == ["session-1"]
    assert persisted == [("session-1", 8)]
    assert deleted == ["cp-future"]


@pytest.mark.asyncio
async def test_runtime_checkpoint_restore_orchestration_runs_restore_under_admission() -> (
    None
):
    session = FakeSession()
    admission = RecordingRestoreAdmission(session)
    restored: list[tuple[FakeSession, str]] = []

    async def restore(restore_session: FakeSession, checkpoint_id: str) -> None:
        restored.append((restore_session, checkpoint_id))

    await RuntimeCheckpointRestoreOrchestrationService(
        admission=admission,
        restore=restore,
    ).restore_checkpoint("session-1", "cp-1")

    assert admission.session_ids == ["session-1"]
    assert restored == [(session, "cp-1")]
