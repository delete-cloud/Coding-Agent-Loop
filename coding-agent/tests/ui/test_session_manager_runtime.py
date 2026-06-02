from __future__ import annotations

import asyncio
import json
import threading
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from agentkit.runtime.context import AgentRunContext
from agentkit.checkpoint.models import CheckpointMeta
from agentkit.tools import FatalToolExecutionError
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from coding_agent.adapter_types import StopReason, TurnOutcome
from coding_agent.agent_observability import JsonlAgentObservationStore
from coding_agent.approval import ApprovalPolicy
from coding_agent.environment import (
    CloudCommandResult,
    CloudEnvironment,
    LocalEnvironment,
)
from coding_agent.executors import (
    LocalDaemonExecutor,
    LocalDaemonRuntimeBinding,
    LocalDaemonRuntimeExecution,
    LocalDaemonRuntimePreparation,
    LocalDaemonRuntimeResult,
    RunExecutorTargetError,
)
from coding_agent.runtime_store import (
    AgentInteractionRecord,
    AgentRunRecord,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
)
from coding_agent.runs import (
    CloudWorkspaceRef,
    IsolationPolicy,
    LocalDaemonExecutorRef,
    LocalPathWorkspaceRef,
    ManagedPoolExecutorRef,
    RunCoordinatorError,
    RunRequest,
    RunSubmission,
    RunTarget,
)
from coding_agent.environment.binding_resolver import DefaultBindingResolver
from coding_agent.environment.execution_binding import (
    CloudWorkspaceBinding,
    ExternalWorkerBinding,
    LocalExecutionBinding,
)
from coding_agent.server.stores.session_owner_store import SessionOwnershipConflictError
from coding_agent.server.stores.session_owner_store import SessionOwnerRecord
from coding_agent.wire.protocol import (
    ApprovalRequest,
    CompletionStatus,
    StreamDelta,
    ToolCallDelta,
    TurnEnd,
)
from coding_agent.server.session_manager import (
    MockProvider,
    SessionManager,
    _load_pg_storage_types,
)
from coding_agent.server.stores.session_store import InMemorySessionStore
from coding_agent.server.stores.workspace_store import (
    JSONValue,
    WorkspaceRecord,
    WorkspaceRetentionPolicy,
    WorkspaceStatus,
)


class FakeRuntimeStore:
    def __init__(self) -> None:
        self.created: list[AgentRunRecord] = []
        self.updated: list[dict[str, object]] = []
        self.events: list[RuntimeEventRecord] = []
        self.snapshots: list[RunMessageSnapshotRecord] = []
        self.interactions: list[AgentInteractionRecord] = []

    async def create_agent_run(self, record: AgentRunRecord) -> AgentRunRecord:
        self.created.append(record)
        return record

    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None:
        records: dict[str, AgentRunRecord] = {
            record.run_id: record for record in self.created
        }
        for update in self.updated:
            record = records.get(cast(str, update["run_id"]))
            if record is None:
                continue
            records[record.run_id] = AgentRunRecord(
                run_id=record.run_id,
                session_id=record.session_id,
                tape_id=record.tape_id,
                parent_run_id=record.parent_run_id,
                agent_id=record.agent_id,
                status=cast(str, update["status"]),
                started_at=record.started_at,
                ended_at=cast(datetime | None, update["ended_at"]),
                metadata=cast(dict[str, JSONValue], update["metadata"]),
                result=cast(dict[str, JSONValue], update["result"]),
                error=cast(str | None, update["error"]),
            )
        return records.get(run_id)

    async def update_agent_run(
        self,
        run_id: str,
        *,
        status: str,
        ended_at: datetime | None,
        metadata: dict[str, object],
        result: dict[str, object],
        error: str | None,
    ) -> AgentRunRecord:
        self.updated.append(
            {
                "run_id": run_id,
                "status": status,
                "ended_at": ended_at,
                "metadata": metadata,
                "result": result,
                "error": error,
            }
        )
        created = self.created[-1]
        return AgentRunRecord(
            run_id=run_id,
            session_id=created.session_id,
            tape_id=created.tape_id,
            parent_run_id=created.parent_run_id,
            agent_id=created.agent_id,
            status=status,
            started_at=created.started_at,
            ended_at=ended_at,
            metadata=metadata,
            result=result,
            error=error,
        )

    async def list_agent_runs(self, session_id: str) -> list[AgentRunRecord]:
        records = [record for record in self.created if record.session_id == session_id]
        for update in self.updated:
            run_id = cast(str, update["run_id"])
            for index, record in enumerate(records):
                if record.run_id != run_id:
                    continue
                records[index] = AgentRunRecord(
                    run_id=record.run_id,
                    session_id=record.session_id,
                    tape_id=record.tape_id,
                    parent_run_id=record.parent_run_id,
                    agent_id=record.agent_id,
                    status=cast(str, update["status"]),
                    started_at=record.started_at,
                    ended_at=cast(datetime | None, update["ended_at"]),
                    metadata=cast(dict[str, JSONValue], update["metadata"]),
                    result=cast(dict[str, JSONValue], update["result"]),
                    error=cast(str | None, update["error"]),
                )
        return records

    async def claim_attached_executor_run(
        self,
        *,
        session_id: str | None,
        executor_kind: str,
        claim_metadata: dict[str, object],
    ) -> AgentRunRecord | None:
        for record in list(self.created):
            if session_id is not None and record.session_id != session_id:
                continue
            if record.status not in {"requested", "expired"}:
                continue
            if record.metadata.get("executor_kind") != executor_kind:
                continue
            claimed = AgentRunRecord(
                run_id=record.run_id,
                session_id=record.session_id,
                tape_id=record.tape_id,
                parent_run_id=record.parent_run_id,
                agent_id=record.agent_id,
                status="claimed",
                started_at=record.started_at,
                ended_at=record.ended_at,
                metadata={**record.metadata, **claim_metadata},
                result=record.result,
                error=record.error,
            )
            self.created[self.created.index(record)] = claimed
            return claimed
        return None

    async def claim_external_worker_run(
        self,
        *,
        session_id: str | None,
        executor_kind: str,
        claim_metadata: dict[str, object],
    ) -> AgentRunRecord | None:
        return await self.claim_attached_executor_run(
            session_id=session_id,
            executor_kind=executor_kind,
            claim_metadata=claim_metadata,
        )

    async def append_runtime_event(
        self,
        record: RuntimeEventRecord,
    ) -> RuntimeEventRecord:
        sequence = len(self.events) + 1
        sequenced = RuntimeEventRecord(
            event_id=record.event_id,
            run_id=record.run_id,
            event_kind=record.event_kind,
            payload=record.payload,
            created_at=record.created_at,
            sequence=sequence,
        )
        self.events.append(sequenced)
        return sequenced

    async def load_runtime_event(
        self,
        event_id: str,
    ) -> RuntimeEventRecord | None:
        for event in self.events:
            if event.event_id == event_id:
                return event
        return None

    async def replay_runtime_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> list[RuntimeEventRecord]:
        events = [
            event
            for event in self.events
            if event.run_id == run_id
            and event.sequence is not None
            and event.sequence > after_sequence
        ]
        return events[:limit]

    async def save_message_snapshot(
        self,
        record: RunMessageSnapshotRecord,
    ) -> RunMessageSnapshotRecord:
        self.snapshots.append(record)
        return record

    async def load_message_snapshot(
        self,
        snapshot_id: str,
    ) -> RunMessageSnapshotRecord | None:
        for snapshot in self.snapshots:
            if snapshot.snapshot_id == snapshot_id:
                return snapshot
        return None

    async def create_agent_interaction(
        self,
        record: AgentInteractionRecord,
    ) -> AgentInteractionRecord:
        for interaction in self.interactions:
            if interaction.interaction_id == record.interaction_id:
                return interaction
        self.interactions.append(record)
        return record

    async def load_agent_interaction(
        self,
        interaction_id: str,
    ) -> AgentInteractionRecord | None:
        for interaction in self.interactions:
            if interaction.interaction_id == interaction_id:
                return interaction
        return None

    async def resolve_agent_interaction(
        self,
        interaction_id: str,
        *,
        status: str,
        response_payload: dict[str, object],
        resolved_at: datetime,
    ) -> AgentInteractionRecord:
        for index, interaction in enumerate(self.interactions):
            if interaction.interaction_id != interaction_id:
                continue
            if interaction.resolved_at is not None:
                return interaction
            resolved = AgentInteractionRecord(
                interaction_id=interaction.interaction_id,
                run_id=interaction.run_id,
                interaction_kind=interaction.interaction_kind,
                status=status,
                request_payload=interaction.request_payload,
                response_payload=response_payload,
                metadata=interaction.metadata,
                created_at=interaction.created_at,
                resolved_at=resolved_at,
            )
            self.interactions[index] = resolved
            return resolved
        raise KeyError(f"agent interaction not found: {interaction_id}")


class RecordingRunCoordinator:
    def __init__(self) -> None:
        self.requests: list[RunRequest] = []
        self.executions: list[LocalDaemonRuntimeExecution] = []
        self.results: list[LocalDaemonRuntimeResult] = []

    async def submit_run(self, request: RunRequest) -> RunSubmission:
        self.requests.append(request)
        return RunSubmission(
            session_id=request.session_id,
            run_id=request.run_id,
            target=request.target,
            executor=request.target.executor,
            metadata=request.metadata,
        )

    async def execute_runtime(
        self,
        execution: LocalDaemonRuntimeExecution,
    ) -> LocalDaemonRuntimeResult:
        self.executions.append(execution)
        result = await LocalDaemonExecutor().execute_runtime(execution)
        self.results.append(result)
        return result


class RejectingRunCoordinator:
    def __init__(self, *, error: str) -> None:
        self.error = error
        self.requests: list[RunRequest] = []
        self.executions: list[LocalDaemonRuntimeExecution] = []

    async def submit_run(self, request: RunRequest) -> RunSubmission:
        self.requests.append(request)
        return RunSubmission(
            session_id=request.session_id,
            run_id=request.run_id,
            target=request.target,
            executor=request.target.executor,
            metadata=request.metadata,
        )

    async def execute_runtime(
        self,
        execution: LocalDaemonRuntimeExecution,
    ) -> LocalDaemonRuntimeResult:
        self.executions.append(execution)
        raise RunCoordinatorError(self.error)


class RecordingLocalDaemonExecutor(LocalDaemonExecutor):
    def __init__(self) -> None:
        self.submissions: list[RunRequest] = []
        self.preparations: list[LocalDaemonRuntimePreparation] = []
        self.executions: list[LocalDaemonRuntimeExecution] = []
        self.results: list[LocalDaemonRuntimeResult] = []

    async def submit_run(self, request: RunRequest) -> RunSubmission:
        self.submissions.append(request)
        return await super().submit_run(request)

    async def prepare_runtime(
        self,
        preparation: LocalDaemonRuntimePreparation,
    ) -> LocalDaemonRuntimeBinding:
        self.preparations.append(preparation)
        return await preparation.runtime_provider.prepare_runtime(preparation.request)

    async def execute_runtime(
        self,
        execution: LocalDaemonRuntimeExecution,
    ) -> LocalDaemonRuntimeResult:
        self.executions.append(execution)
        result = await super().execute_runtime(execution)
        self.results.append(result)
        return result


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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "hello")

    assert captured_kwargs["session_id_override"] == session_id
    assert captured_kwargs["api_key"] is None


@pytest.mark.asyncio
async def test_run_agent_creates_run_id_and_preserves_current_turn_id_alias() -> None:
    manager = SessionManager()
    session_id = await manager.create_session()

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt
            observed_run_ids.append(self.ctx.run_context.run_id)

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )
    captured_kwargs: dict[str, object] = {}
    observed_run_ids: list[str] = []

    def fake_create_agent(**kwargs):
        captured_kwargs.update(kwargs)
        environment = kwargs["environment"]
        if not isinstance(environment, LocalEnvironment):
            raise TypeError("expected local environment")
        return fake_pipeline, types.SimpleNamespace(
            session_id="stale-session",
            config={},
            tape=kwargs.get("tape") or Tape(),
            run_context=AgentRunContext(
                session_id="stale-session",
                run_id="stale-run",
                agent_id=None,
                parent_run_id="old-parent",
                environment=environment,
            ),
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "hello")

    run_id = captured_kwargs["run_id_override"]
    assert isinstance(run_id, str)
    assert run_id
    assert captured_kwargs["session_id_override"] == session_id
    session = manager.get_session(session_id)
    assert session.current_turn_id == run_id
    assert observed_run_ids == [run_id]


@pytest.mark.asyncio
async def test_run_agent_records_turn_started_before_adapter_finishes(tmp_path) -> None:
    observation_store = JsonlAgentObservationStore(tmp_path)
    manager = SessionManager(observation_store=observation_store)
    session_id = await manager.create_session()
    adapter_started = asyncio.Event()
    release_adapter = asyncio.Event()
    observed_run_ids: list[str] = []

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> TurnOutcome:
            del prompt
            observed_run_ids.append(self.ctx.run_context.run_id)
            adapter_started.set()
            await release_adapter.wait()
            return TurnOutcome(stop_reason=StopReason.NO_TOOL_CALLS)

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        environment = kwargs["environment"]
        if not isinstance(environment, LocalEnvironment):
            raise TypeError("expected local environment")
        return fake_pipeline, types.SimpleNamespace(
            session_id="stale-session",
            config={},
            tape=kwargs.get("tape") or Tape(),
            run_context=AgentRunContext(
                session_id="stale-session",
                run_id="stale-run",
                agent_id=None,
                parent_run_id=None,
                environment=environment,
            ),
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)

        run_task = asyncio.create_task(manager.run_agent(session_id, "raw prompt"))
        await asyncio.wait_for(adapter_started.wait(), timeout=1.0)

        run_id = observed_run_ids[0]
        observation_path = tmp_path / "runs" / run_id / "observations.jsonl"
        body = observation_path.read_text(encoding="utf-8")
        assert "turn.started" in body
        assert "raw prompt" not in body

        release_adapter.set()
        await run_task


@pytest.mark.asyncio
async def test_run_agent_persists_agent_run_lifecycle_when_store_configured() -> None:
    runtime_store = FakeRuntimeStore()
    manager = SessionManager(runtime_store=runtime_store)
    session_id = await manager.create_session(
        provider=MockProvider(),
        provider_name="test-provider",
        model_name="test-model",
        base_url="https://user:pass@example.invalid",
    )

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> TurnOutcome:
            del prompt
            return TurnOutcome(
                stop_reason=StopReason.NO_TOOL_CALLS,
                final_message="do not persist raw final text",
                steps_taken=2,
            )

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        environment = kwargs["environment"]
        if not isinstance(environment, LocalEnvironment):
            raise TypeError("expected local environment")
        return fake_pipeline, types.SimpleNamespace(
            session_id=kwargs["session_id_override"],
            config={},
            tape=kwargs.get("tape") or Tape(tape_id="stable-tape"),
            run_context=AgentRunContext(
                session_id=kwargs["session_id_override"],
                run_id=kwargs["run_id_override"],
                agent_id=None,
                environment=environment,
            ),
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "hello")

    session = manager.get_session(session_id)
    assert len(runtime_store.created) == 1
    assert len(runtime_store.updated) == 2
    created = runtime_store.created[0]
    running_update = runtime_store.updated[0]
    completed_update = runtime_store.updated[1]
    assert created.run_id == session.current_turn_id
    assert created.session_id == session_id
    assert created.tape_id == "stable-tape"
    assert created.status == "queued"
    assert created.metadata == {
        "provider_name": "test-provider",
        "model_name": "test-model",
        "approval_policy": "auto",
        "max_steps": 30,
        "execution_binding_kind": "local",
        "workspace_surface": "local_workspace",
        "execution_plane": "control_plane",
        "execution_placement": "server_embedded",
    }
    assert running_update == {
        "run_id": created.run_id,
        "status": "running",
        "ended_at": None,
        "metadata": created.metadata,
        "result": {},
        "error": None,
    }
    assert completed_update["run_id"] == created.run_id
    assert completed_update["status"] == "completed"
    assert completed_update["ended_at"] is not None
    assert completed_update["metadata"] == created.metadata
    assert completed_update["result"] == {
        "stop_reason": "no_tool_calls",
        "steps_taken": 2,
    }
    assert "final_message" not in completed_update["result"]
    assert completed_update["error"] is None


@pytest.mark.asyncio
async def test_run_agent_submits_run_request_to_run_coordinator(
    tmp_path: Path,
) -> None:
    runtime_store = FakeRuntimeStore()
    run_coordinator = RecordingRunCoordinator()
    legacy_workspace = tmp_path / "legacy-repo"
    legacy_workspace.mkdir()
    target_workspace = tmp_path / "target-repo"
    target_workspace.mkdir()
    manager = SessionManager(
        runtime_store=cast(Any, runtime_store),
        run_coordinator=run_coordinator,
    )
    session_id = await manager.create_session(
        execution_binding=LocalExecutionBinding(workspace_root=str(legacy_workspace)),
    )
    session = manager.get_session(session_id)
    session.default_run_target = RunTarget(
        workspace=LocalPathWorkspaceRef(path=str(target_workspace.resolve())),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="default_local_sandbox"),
        annotations={"source": "session-default"},
    )

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> TurnOutcome:
            del prompt
            return TurnOutcome(
                stop_reason=StopReason.NO_TOOL_CALLS,
                steps_taken=1,
            )

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        environment = kwargs["environment"]
        if not isinstance(environment, LocalEnvironment):
            raise TypeError("expected local environment")
        assert environment.workspace_root == target_workspace.resolve()
        return fake_pipeline, types.SimpleNamespace(
            session_id=kwargs["session_id_override"],
            config={},
            tape=kwargs.get("tape") or Tape(tape_id="stable-tape"),
            run_context=AgentRunContext(
                session_id=kwargs["session_id_override"],
                run_id=kwargs["run_id_override"],
                agent_id=None,
                environment=environment,
            ),
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "implement coordinator integration")

    created_run = runtime_store.created[0]
    assert len(run_coordinator.requests) == 1
    request = run_coordinator.requests[0]
    assert request.session_id == session_id
    assert request.run_id == created_run.run_id
    assert request.input_summary == "implement coordinator integration"
    assert request.resume_from_run_id is None
    assert request.target.annotations == {"source": "session-default"}
    assert isinstance(request.target.executor, LocalDaemonExecutorRef)
    assert isinstance(request.target.workspace, LocalPathWorkspaceRef)
    assert request.target.workspace.path == str(target_workspace.resolve())
    assert len(run_coordinator.executions) == 1
    execution = run_coordinator.executions[0]
    assert execution.request is request
    assert execution.prompt == "implement coordinator integration"
    assert len(run_coordinator.results) == 1
    assert runtime_store.updated[0]["status"] == "running"
    assert runtime_store.updated[-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_run_agent_executes_local_runtime_through_local_daemon_executor(
    tmp_path: Path,
) -> None:
    runtime_store = FakeRuntimeStore()
    local_executor = RecordingLocalDaemonExecutor()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    manager = SessionManager(
        runtime_store=cast(Any, runtime_store),
        local_daemon_executor=local_executor,
    )
    session_id = await manager.create_session(
        execution_binding=LocalExecutionBinding(workspace_root=str(workspace)),
    )

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> TurnOutcome:
            del prompt
            return TurnOutcome(
                stop_reason=StopReason.NO_TOOL_CALLS,
                steps_taken=1,
            )

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        environment = kwargs["environment"]
        if not isinstance(environment, LocalEnvironment):
            raise TypeError("expected local environment")
        return fake_pipeline, types.SimpleNamespace(
            session_id=kwargs["session_id_override"],
            config={},
            tape=kwargs.get("tape") or Tape(tape_id="stable-tape"),
            run_context=AgentRunContext(
                session_id=kwargs["session_id_override"],
                run_id=kwargs["run_id_override"],
                agent_id=None,
                environment=environment,
            ),
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "implement runtime ownership")

    created_run = runtime_store.created[0]
    assert len(local_executor.submissions) == 1
    submission_request = local_executor.submissions[0]
    assert submission_request.session_id == session_id
    assert submission_request.run_id == created_run.run_id
    assert submission_request.input_summary == "implement runtime ownership"
    assert isinstance(submission_request.target.executor, LocalDaemonExecutorRef)
    assert isinstance(submission_request.target.workspace, LocalPathWorkspaceRef)
    assert submission_request.target.workspace.path == str(workspace.resolve())
    assert len(local_executor.executions) == 1
    execution = local_executor.executions[0]
    assert execution.request.session_id == session_id
    assert execution.request.run_id == created_run.run_id
    assert execution.request.input_summary == "implement runtime ownership"
    assert execution.prompt == "implement runtime ownership"
    assert execution.before_turn is not None
    assert execution.after_turn is not None
    assert execution.on_turn_error is not None
    assert isinstance(execution.request.target.executor, LocalDaemonExecutorRef)
    assert isinstance(execution.request.target.workspace, LocalPathWorkspaceRef)
    assert execution.request.target.workspace.path == str(workspace.resolve())
    assert len(local_executor.results) == 1
    assert isinstance(local_executor.results[0].binding.adapter, FakeAdapter)
    assert runtime_store.updated[0]["status"] == "running"
    assert runtime_store.updated[-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_run_agent_marks_agent_run_failed_when_turn_outcome_errors() -> None:
    runtime_store = FakeRuntimeStore()
    manager = SessionManager(runtime_store=runtime_store)
    session_id = await manager.create_session()

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer

        async def run_turn(self, prompt: str) -> TurnOutcome:
            del prompt
            return TurnOutcome(stop_reason=StopReason.ERROR, error="model failed")

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        environment = kwargs["environment"]
        if not isinstance(environment, LocalEnvironment):
            raise TypeError("expected local environment")
        return fake_pipeline, types.SimpleNamespace(
            session_id=kwargs["session_id_override"],
            config={},
            tape=kwargs.get("tape") or Tape(tape_id="stable-tape"),
            run_context=AgentRunContext(
                session_id=kwargs["session_id_override"],
                run_id=kwargs["run_id_override"],
                agent_id=None,
                environment=environment,
            ),
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "hello")

    assert runtime_store.updated[0]["status"] == "running"
    assert runtime_store.updated[-1]["status"] == "failed"
    assert runtime_store.updated[-1]["result"] == {
        "stop_reason": "error",
        "steps_taken": 0,
    }
    assert runtime_store.updated[-1]["error"] == "model failed"
    session = await manager.get_session_async(session_id)
    assert session.turn_status == "failed"
    assert session.last_failure_details == "Agent turn failed: model failed"


@pytest.mark.asyncio
async def test_run_agent_records_error_outcome_without_runtime_store() -> None:
    manager = SessionManager()
    session_id = await manager.create_session()

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, ctx, consumer

        async def run_turn(self, prompt: str) -> TurnOutcome:
            del prompt
            return TurnOutcome(stop_reason=StopReason.ERROR, error="provider failed")

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        environment = kwargs["environment"]
        if not isinstance(environment, LocalEnvironment):
            raise TypeError("expected local environment")
        return fake_pipeline, types.SimpleNamespace(
            session_id=kwargs["session_id_override"],
            config={},
            tape=kwargs.get("tape") or Tape(tape_id="stable-tape"),
            run_context=AgentRunContext(
                session_id=kwargs["session_id_override"],
                run_id=kwargs["run_id_override"],
                agent_id=None,
                environment=environment,
            ),
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "hello")

    session = await manager.get_session_async(session_id)
    assert session.turn_status == "failed"
    assert session.last_failure_details == "Agent turn failed: provider failed"


@pytest.mark.asyncio
async def test_agent_run_marks_interrupted_outcome_as_interrupted() -> None:
    runtime_store = FakeRuntimeStore()
    manager = SessionManager(runtime_store=runtime_store)
    session_id = await manager.create_session()

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, ctx, consumer

        async def run_turn(self, prompt: str) -> TurnOutcome:
            del prompt
            return TurnOutcome(
                stop_reason=StopReason.INTERRUPTED,
                error="manual interrupt",
                steps_taken=1,
            )

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        environment = kwargs["environment"]
        if not isinstance(environment, LocalEnvironment):
            raise TypeError("expected local environment")
        return fake_pipeline, types.SimpleNamespace(
            session_id=kwargs["session_id_override"],
            config={},
            tape=kwargs.get("tape") or Tape(tape_id="stable-tape"),
            run_context=AgentRunContext(
                session_id=kwargs["session_id_override"],
                run_id=kwargs["run_id_override"],
                agent_id=None,
                environment=environment,
            ),
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "hello")

    assert runtime_store.created[0].status == "queued"
    assert runtime_store.updated[0]["status"] == "running"
    assert runtime_store.updated[-1]["status"] == "interrupted"
    assert runtime_store.updated[-1]["result"] == {
        "stop_reason": "interrupted",
        "steps_taken": 1,
    }
    assert runtime_store.updated[-1]["error"] == "manual interrupt"


@pytest.mark.asyncio
async def test_resume_session_creates_new_run_linked_to_interrupted_run(
    tmp_path: Path,
) -> None:
    runtime_store = FakeRuntimeStore()
    observation_store = JsonlAgentObservationStore(tmp_path)
    manager = SessionManager(
        runtime_store=runtime_store,
        observation_store=observation_store,
    )
    session_id = await manager.create_session()
    session = await manager.get_session_async(session_id)
    stable_tape_id = f"{session_id}-stable-tape"
    session.tape_id = stable_tape_id
    interrupted_at = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)
    previous_run = AgentRunRecord(
        run_id="run-interrupted",
        session_id=session_id,
        tape_id=stable_tape_id,
        parent_run_id=None,
        agent_id=None,
        status="interrupted",
        started_at=interrupted_at - timedelta(minutes=5),
        ended_at=interrupted_at,
        metadata={"provider_name": "test-provider"},
        result={"steps_taken": 2},
        error="runtime interrupted",
    )
    runtime_store.created.append(previous_run)
    await manager._tape_store.save(
        stable_tape_id,
        [
            Entry(
                kind="message",
                payload={"role": "user", "content": "implement resume"},
                id="entry-user",
            ).to_dict(),
            Entry(
                kind="tool_call",
                payload={
                    "tool_name": "todo_write",
                    "tasks": [
                        {
                            "title": "Wire resume context to tape tail",
                            "status": "in_progress",
                        }
                    ],
                },
                id="entry-plan",
            ).to_dict(),
            Entry(
                kind="message",
                payload={"role": "assistant", "content": "partial progress"},
                id="entry-assistant",
            ).to_dict(),
        ],
    )
    await runtime_store.append_runtime_event(
        RuntimeEventRecord(
            event_id="event-last",
            run_id=previous_run.run_id,
            event_kind="wire.StreamDelta",
            payload={"content": "partial"},
            created_at=interrupted_at,
        )
    )
    await runtime_store.save_message_snapshot(
        RunMessageSnapshotRecord(
            snapshot_id=f"{previous_run.run_id}:latest",
            run_id=previous_run.run_id,
            messages=[
                {"role": "user", "content": "implement resume"},
                {"role": "assistant", "content": "partial progress"},
            ],
            metadata={
                "session_id": session_id,
                "tape_id": stable_tape_id,
                "snapshot_kind": "latest_context",
            },
            created_at=interrupted_at,
        )
    )
    observed_prompt: list[str] = []

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, ctx, consumer

        async def run_turn(self, prompt: str) -> TurnOutcome:
            observed_prompt.append(prompt)
            return TurnOutcome(stop_reason=StopReason.NO_TOOL_CALLS)

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        environment = kwargs["environment"]
        if not isinstance(environment, LocalEnvironment):
            raise TypeError("expected local environment")
        return fake_pipeline, types.SimpleNamespace(
            session_id=kwargs["session_id_override"],
            config={},
            tape=kwargs.get("tape") or Tape(tape_id=stable_tape_id),
            run_context=AgentRunContext(
                session_id=kwargs["session_id_override"],
                run_id=kwargs["run_id_override"],
                agent_id=None,
                environment=environment,
            ),
        )

    class FakeCheckpointService:
        async def list(self, tape_id: str):
            assert tape_id == session.tape_id
            return [
                CheckpointMeta(
                    checkpoint_id="cp-latest",
                    tape_id=tape_id,
                    session_id=session_id,
                    entry_count=2,
                    window_start=0,
                    created_at=interrupted_at + timedelta(seconds=1),
                    label="latest",
                )
            ]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(manager, "_checkpoint_service", FakeCheckpointService())

        new_run = await manager.resume_session(
            session_id,
            prompt="continue the implementation",
            resume_reason="user_resume",
        )

    assert new_run.run_id != previous_run.run_id
    assert new_run.parent_run_id == previous_run.run_id
    assert new_run.metadata["previous_run_id"] == previous_run.run_id
    assert new_run.metadata["resume_from_run_id"] == previous_run.run_id
    assert new_run.metadata["resume_from_event_id"] == "event-last"
    assert new_run.metadata["resume_reason"] == "user_resume"
    assert new_run.metadata["resume_context_injected"] is True
    assert new_run.metadata["resume_context_strategy"] == (
        "checkpoint+tape_tail+message_snapshot"
    )
    assert isinstance(new_run.metadata["resume_boundary_anchor_id"], str)
    assert new_run.metadata["resume_boundary_anchor_type"] == "resume_boundary"
    assert new_run.metadata["checkpoint_count"] == 1
    assert new_run.metadata["latest_checkpoint_id"] == "cp-latest"
    assert new_run.metadata["latest_checkpoint_label"] == "latest"
    assert new_run.metadata["tape_entry_count"] == 3
    assert new_run.metadata["resume_tape_tail_entry_count"] == 3
    assert new_run.metadata["resume_plan_note_included"] is True
    assert new_run.metadata["latest_message_snapshot_id"] == "run-interrupted:latest"
    assert new_run.metadata["latest_message_snapshot_message_count"] == 2
    assert runtime_store.created[0] == previous_run
    assert runtime_store.created[1].run_id == new_run.run_id
    assert runtime_store.updated[-1]["status"] == "completed"
    assert "Previous run was interrupted." in observed_prompt[0]
    assert "Latest checkpoint: cp-latest (latest)." in observed_prompt[0]
    assert (
        "Latest runtime message snapshot: run-interrupted:latest (2 messages)."
        in observed_prompt[0]
    )
    assert "Latest tape tail (3 of 3 entries):" in observed_prompt[0]
    assert "todo_write" in observed_prompt[0]
    assert "Wire resume context to tape tail" in observed_prompt[0]
    assert "Latest plan/checkpoint note:" in observed_prompt[0]
    assert "it does not restore or roll back to a checkpoint" in observed_prompt[0]
    assert "continue the implementation" in observed_prompt[0]
    assert "resume_boundary" not in observed_prompt[0]
    tape_entries = await manager._tape_store.load(stable_tape_id)
    boundary_entry = tape_entries[3]
    assert len(tape_entries) == 4
    assert boundary_entry["kind"] == "anchor"
    assert boundary_entry["anchor_type"] == "context"
    assert boundary_entry["id"] == new_run.metadata["resume_boundary_anchor_id"]
    assert boundary_entry["payload"] == {"label": "Resume boundary"}
    boundary_meta = cast(dict[str, object], boundary_entry["meta"])
    assert boundary_meta["product_anchor_type"] == "resume_boundary"
    assert boundary_meta["skip"] is True
    assert boundary_meta["previous_run_id"] == previous_run.run_id
    assert boundary_meta["resume_from_run_id"] == previous_run.run_id
    assert boundary_meta["resume_from_event_id"] == "event-last"
    assert boundary_meta["resume_reason"] == "user_resume"
    assert boundary_meta["resume_context_strategy"] == (
        "checkpoint+tape_tail+message_snapshot"
    )
    observation_path = tmp_path / "runs" / new_run.run_id / "observations.jsonl"
    observation = json.loads(
        observation_path.read_text(encoding="utf-8").splitlines()[0]
    )
    assert observation["attributes"]["previous_run_id"] == previous_run.run_id
    assert observation["attributes"]["resume_from_run_id"] == previous_run.run_id
    assert observation["attributes"]["resume_from_event_id"] == "event-last"
    assert (
        observation["attributes"]["resume_boundary_anchor_id"]
        == (new_run.metadata["resume_boundary_anchor_id"])
    )
    assert observation["attributes"]["resume_boundary_anchor_type"] == (
        "resume_boundary"
    )
    assert observation["attributes"]["tape_id"] == stable_tape_id
    assert observation["attributes"]["execution_placement"] == "server_embedded"
    assert observation["attributes"]["execution_binding_kind"] == "local"
    assert observation["attributes"]["workspace_surface"] == "local_workspace"
    assert observation["attributes"]["execution_plane"] == "control_plane"


@pytest.mark.asyncio
async def test_resume_session_rejects_active_runtime_run() -> None:
    runtime_store = FakeRuntimeStore()
    manager = SessionManager(runtime_store=runtime_store)
    session_id = await manager.create_session()
    runtime_store.created.append(
        AgentRunRecord(
            run_id="run-active",
            session_id=session_id,
            tape_id="stable-tape",
            parent_run_id=None,
            agent_id=None,
            status="running",
            started_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
            metadata={},
            result={},
        )
    )

    with pytest.raises(RuntimeError, match="latest run is still active"):
        await manager.resume_session(session_id)


@pytest.mark.asyncio
async def test_resume_external_executor_session_requests_linked_run() -> None:
    runtime_store = FakeRuntimeStore()
    manager = SessionManager(runtime_store=runtime_store)
    session_id = await manager.create_session(
        execution_binding=ExternalWorkerBinding(executor_kind="local_cli")
    )
    stable_tape_id = f"{session_id}-tape"
    previous_run = AgentRunRecord(
        run_id="run-cancelled",
        session_id=session_id,
        tape_id=stable_tape_id,
        parent_run_id=None,
        agent_id=None,
        status="cancelled",
        started_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
        ended_at=datetime(2026, 5, 19, 12, 1, tzinfo=UTC),
        metadata={},
        result={},
        error="cancelled",
    )
    runtime_store.created.append(previous_run)

    new_run = await manager.resume_session(
        session_id,
        prompt="resume locally",
        resume_reason="remote_resume",
    )

    assert new_run.status == "requested"
    assert new_run.parent_run_id == previous_run.run_id
    assert new_run.metadata["prompt"].startswith("Previous run was interrupted.")
    assert new_run.metadata["previous_run_id"] == previous_run.run_id
    assert new_run.metadata["resume_from_run_id"] == previous_run.run_id
    assert new_run.metadata["resume_reason"] == "remote_resume"
    assert isinstance(new_run.metadata["resume_boundary_anchor_id"], str)
    assert new_run.metadata["resume_boundary_anchor_type"] == "resume_boundary"
    tape_entries = await manager._tape_store.load(stable_tape_id)
    assert len(tape_entries) == 1
    boundary_entry = tape_entries[0]
    assert boundary_entry["kind"] == "anchor"
    assert boundary_entry["anchor_type"] == "context"
    assert boundary_entry["id"] == new_run.metadata["resume_boundary_anchor_id"]
    boundary_meta = cast(dict[str, object], boundary_entry["meta"])
    assert boundary_meta["product_anchor_type"] == "resume_boundary"
    assert boundary_meta["previous_run_id"] == previous_run.run_id
    assert boundary_meta["resume_reason"] == "remote_resume"


@pytest.mark.asyncio
async def test_session_resume_metadata_reports_run_and_checkpoint_context() -> None:
    runtime_store = FakeRuntimeStore()
    manager = SessionManager(runtime_store=runtime_store)
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.tape_id = "stable-tape"
    await manager._persist_session_async(session)
    previous_run = AgentRunRecord(
        run_id="run-interrupted",
        session_id=session_id,
        tape_id="stable-tape",
        parent_run_id=None,
        agent_id=None,
        status="interrupted",
        started_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
        ended_at=datetime(2026, 5, 19, 12, 1, tzinfo=UTC),
        metadata={},
        result={},
        error="interrupted",
    )
    runtime_store.created.append(previous_run)
    await runtime_store.append_runtime_event(
        RuntimeEventRecord(
            event_id="event-last",
            run_id=previous_run.run_id,
            event_kind="wire.TurnEnd",
            payload={"message_type": "TurnEnd"},
            created_at=datetime(2026, 5, 19, 12, 1, tzinfo=UTC),
        )
    )

    class FakeCheckpointService:
        async def list(self, tape_id: str):
            assert tape_id == "stable-tape"
            return [
                CheckpointMeta(
                    checkpoint_id="cp-old",
                    tape_id=tape_id,
                    session_id=session_id,
                    entry_count=1,
                    window_start=0,
                    created_at=datetime(2026, 5, 19, 11, 0, tzinfo=UTC),
                    label="old",
                ),
                CheckpointMeta(
                    checkpoint_id="cp-latest",
                    tape_id=tape_id,
                    session_id=session_id,
                    entry_count=2,
                    window_start=0,
                    created_at=datetime(2026, 5, 19, 12, 2, tzinfo=UTC),
                    label="latest",
                ),
            ]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(manager, "_checkpoint_service", FakeCheckpointService())
        metadata = await manager.session_resume_metadata(session_id)

    assert metadata == {
        "resumable": True,
        "last_run_id": "run-interrupted",
        "last_run_status": "interrupted",
        "last_interrupted_run_id": "run-interrupted",
        "resume_from_event_id": "event-last",
        "checkpoint_count": 2,
        "latest_checkpoint_id": "cp-latest",
        "latest_checkpoint_label": "latest",
    }


@pytest.mark.asyncio
async def test_recover_stale_runtime_runs_marks_running_runs_interrupted_reclaimable() -> (
    None
):
    runtime_store = FakeRuntimeStore()
    manager = SessionManager(runtime_store=runtime_store)
    session_id = await manager.create_session()
    recovered_at = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)
    started_at = datetime(2026, 5, 19, 11, 0, tzinfo=UTC)
    runtime_store.created.extend(
        [
            AgentRunRecord(
                run_id="run-stale",
                session_id=session_id,
                tape_id="tape-1",
                parent_run_id=None,
                agent_id=None,
                status="running",
                started_at=started_at,
                metadata={"provider_name": "test-provider"},
                result={"steps_taken": 1},
            ),
            AgentRunRecord(
                run_id="run-complete",
                session_id=session_id,
                tape_id="tape-1",
                parent_run_id=None,
                agent_id=None,
                status="completed",
                started_at=started_at,
                ended_at=recovered_at,
                metadata={},
                result={},
            ),
        ]
    )

    recovered_count = await manager.recover_stale_runtime_runs(
        recovered_at=recovered_at
    )

    assert recovered_count == 1
    assert runtime_store.updated == [
        {
            "run_id": "run-stale",
            "status": "interrupted",
            "ended_at": recovered_at,
            "metadata": {
                "provider_name": "test-provider",
                "reclaimable": True,
                "recovered_at": recovered_at.isoformat(),
                "recovery_reason": "startup_stale_running_run",
            },
            "result": {"steps_taken": 1},
            "error": "runtime run was still running during startup recovery",
        }
    ]


class FakeOwnerStore:
    def __init__(self, owners: dict[str, SessionOwnerRecord]) -> None:
        self.owners = owners

    async def acquire(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float = 30.0,
        fencing_token: int = 1,
    ) -> bool:
        del session_id, owner_id, lease_seconds, fencing_token
        return False

    async def renew(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float = 30.0,
        new_fencing_token: int = 2,
        current_fencing_token: int = 1,
    ) -> bool:
        del session_id, owner_id, lease_seconds, new_fencing_token
        del current_fencing_token
        return False

    async def release(
        self,
        session_id: str,
        owner_id: str,
        fencing_token: int,
    ) -> bool:
        del session_id, owner_id, fencing_token
        return False

    async def get_owner(self, session_id: str) -> SessionOwnerRecord | None:
        return self.owners.get(session_id)


@pytest.mark.asyncio
async def test_recover_stale_runtime_runs_skips_sessions_without_current_owner() -> (
    None
):
    runtime_store = FakeRuntimeStore()
    manager = SessionManager(runtime_store=runtime_store)
    owned_session_id = await manager.create_session()
    foreign_session_id = await manager.create_session()
    expired_session_id = await manager.create_session()
    now = datetime.now(UTC)
    manager.configure_owner_leases(
        owner_store=FakeOwnerStore(
            {
                owned_session_id: SessionOwnerRecord(
                    owner_id="pod-a",
                    lease_expires_at=now + timedelta(seconds=30),
                    fencing_token=7,
                ),
                foreign_session_id: SessionOwnerRecord(
                    owner_id="pod-b",
                    lease_expires_at=now + timedelta(seconds=30),
                    fencing_token=8,
                ),
                expired_session_id: SessionOwnerRecord(
                    owner_id="pod-a",
                    lease_expires_at=now - timedelta(seconds=1),
                    fencing_token=7,
                ),
            }
        ),
        owner_id="pod-a",
        fencing_token=7,
    )
    recovered_at = datetime(2026, 5, 19, 12, 30, tzinfo=UTC)
    started_at = datetime(2026, 5, 19, 11, 30, tzinfo=UTC)
    runtime_store.created.extend(
        [
            AgentRunRecord(
                run_id="run-owned",
                session_id=owned_session_id,
                tape_id="tape-owned",
                parent_run_id=None,
                agent_id=None,
                status="running",
                started_at=started_at,
            ),
            AgentRunRecord(
                run_id="run-foreign",
                session_id=foreign_session_id,
                tape_id="tape-foreign",
                parent_run_id=None,
                agent_id=None,
                status="running",
                started_at=started_at,
            ),
            AgentRunRecord(
                run_id="run-expired",
                session_id=expired_session_id,
                tape_id="tape-expired",
                parent_run_id=None,
                agent_id=None,
                status="running",
                started_at=started_at,
            ),
        ]
    )

    recovered_count = await manager.recover_stale_runtime_runs(
        recovered_at=recovered_at
    )

    assert recovered_count == 1
    assert runtime_store.updated[0]["run_id"] == "run-owned"
    assert runtime_store.updated[0]["metadata"] == {
        "reclaimable": True,
        "recovered_at": recovered_at.isoformat(),
        "recovered_by_owner_id": "pod-a",
        "recovery_reason": "startup_stale_running_run",
    }


@pytest.mark.asyncio
async def test_run_agent_persists_wire_events_when_runtime_store_configured() -> None:
    runtime_store = FakeRuntimeStore()
    manager = SessionManager(runtime_store=runtime_store)
    session_id = await manager.create_session()
    emitted_at = datetime(2026, 1, 2, 3, 4, 5)

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline
            self.ctx = ctx
            self.consumer = consumer

        async def run_turn(self, prompt: str) -> TurnOutcome:
            del prompt
            await self.consumer.emit(
                StreamDelta(
                    session_id=session_id,
                    agent_id="root",
                    timestamp=emitted_at,
                    content="hello",
                )
            )
            return TurnOutcome(stop_reason=StopReason.NO_TOOL_CALLS, steps_taken=1)

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        environment = kwargs["environment"]
        if not isinstance(environment, LocalEnvironment):
            raise TypeError("expected local environment")
        return fake_pipeline, types.SimpleNamespace(
            session_id=kwargs["session_id_override"],
            config={},
            tape=kwargs.get("tape") or Tape(),
            run_context=AgentRunContext(
                session_id=kwargs["session_id_override"],
                run_id=kwargs["run_id_override"],
                agent_id=None,
                environment=environment,
            ),
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "hello")

    assert len(runtime_store.events) == 1
    event = runtime_store.events[0]
    assert event.run_id == runtime_store.created[0].run_id
    assert event.event_kind == "wire.StreamDelta"
    assert event.created_at == emitted_at
    assert event.payload["session_id"] == session_id
    assert event.payload["run_id"] == runtime_store.created[0].run_id
    assert event.payload["tape_id"] == runtime_store.created[0].tape_id
    assert event.payload["execution_placement"] == "server_embedded"
    assert event.payload["execution_binding_kind"] == "local"
    assert event.payload["workspace_surface"] == "local_workspace"
    assert event.payload["execution_plane"] == "control_plane"
    assert event.payload["message_type"] == "StreamDelta"
    assert event.payload["message"] == {
        "session_id": session_id,
        "agent_id": "root",
        "timestamp": emitted_at.isoformat(),
        "content": "hello",
        "role": "assistant",
    }


@pytest.mark.asyncio
async def test_run_agent_persists_approval_request_wire_events() -> None:
    runtime_store = FakeRuntimeStore()
    manager = SessionManager(runtime_store=runtime_store)
    session_id = await manager.create_session(provider=MockProvider())
    requested_at = datetime(2026, 1, 2, 3, 4, 5)
    tool_called_at = datetime(2026, 1, 2, 3, 4, 4)
    req = ApprovalRequest(
        session_id=session_id,
        request_id="req-runtime-event",
        timestamp=requested_at,
        tool_call=ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={"command": "pwd"},
            call_id="call-runtime-event",
            timestamp=tool_called_at,
        ),
        timeout_seconds=0,
    )

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline
            self.ctx = ctx
            self.consumer = consumer

        async def run_turn(self, prompt: str) -> TurnOutcome:
            del prompt
            await self.consumer.request_approval(req)
            return TurnOutcome(stop_reason=StopReason.NO_TOOL_CALLS, steps_taken=1)

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        environment = kwargs["environment"]
        if not isinstance(environment, LocalEnvironment):
            raise TypeError("expected local environment")
        return fake_pipeline, types.SimpleNamespace(
            session_id=kwargs["session_id_override"],
            config={},
            tape=kwargs.get("tape") or Tape(tape_id="stable-tape"),
            run_context=AgentRunContext(
                session_id=kwargs["session_id_override"],
                run_id=kwargs["run_id_override"],
                agent_id=None,
                environment=environment,
            ),
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "needs approval")

    assert len(runtime_store.events) == 1
    event = runtime_store.events[0]
    assert event.run_id == runtime_store.created[0].run_id
    assert event.event_kind == "wire.ApprovalRequest"
    assert event.created_at == requested_at
    assert event.payload["session_id"] == session_id
    assert event.payload["run_id"] == runtime_store.created[0].run_id
    assert event.payload["tape_id"] == runtime_store.created[0].tape_id
    assert event.payload["execution_placement"] == "server_embedded"
    assert event.payload["execution_binding_kind"] == "local"
    assert event.payload["workspace_surface"] == "local_workspace"
    assert event.payload["execution_plane"] == "control_plane"
    assert event.payload["message_type"] == "ApprovalRequest"
    assert event.payload["message"] == {
        "session_id": session_id,
        "agent_id": "",
        "timestamp": requested_at.isoformat(),
        "request_id": "req-runtime-event",
        "tool_call": {
            "session_id": session_id,
            "agent_id": "",
            "timestamp": tool_called_at.isoformat(),
            "tool_name": "bash",
            "arguments": {"command": "pwd"},
            "call_id": "call-runtime-event",
        },
        "timeout_seconds": 0,
        "call_id": "req-runtime-event",
        "tool": "bash",
        "args": {"command": "pwd"},
        "risk_level": "low",
    }


@pytest.mark.asyncio
async def test_wait_for_http_approval_persists_runtime_approval_interaction() -> None:
    runtime_store = FakeRuntimeStore()
    manager = SessionManager(runtime_store=runtime_store)
    session_id = await manager.create_session(provider=MockProvider())
    session = manager.get_session(session_id)
    session.turn_in_progress = True
    session.current_turn_id = "run-approval"
    requested_at = datetime(2026, 1, 2, 3, 4, 5)
    tool_called_at = datetime(2026, 1, 2, 3, 4, 4)
    req = ApprovalRequest(
        session_id=session_id,
        request_id="req-runtime-interaction",
        timestamp=requested_at,
        tool_call=ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={"command": "pwd"},
            call_id="call-runtime-interaction",
            timestamp=tool_called_at,
        ),
        timeout_seconds=5,
    )

    wait_task = asyncio.create_task(
        manager.wait_for_http_approval(session_id, req, timeout_seconds=5)
    )
    for _ in range(50):
        if runtime_store.interactions:
            break
        await asyncio.sleep(0)

    assert len(runtime_store.interactions) == 1
    pending = runtime_store.interactions[0]
    assert pending.interaction_id == "run-approval:approval:req-runtime-interaction"
    assert pending.run_id == "run-approval"
    assert pending.interaction_kind == "approval"
    assert pending.status == "pending"
    assert pending.request_payload == {
        "session_id": session_id,
        "request_id": "req-runtime-interaction",
        "timestamp": requested_at.isoformat(),
        "timeout_seconds": 5,
        "tool_call": {
            "session_id": session_id,
            "agent_id": "",
            "timestamp": tool_called_at.isoformat(),
            "tool_name": "bash",
            "arguments": {"command": "pwd"},
            "call_id": "call-runtime-interaction",
        },
    }
    assert pending.metadata == {
        "session_id": session_id,
        "request_id": "req-runtime-interaction",
        "tool_call_id": "call-runtime-interaction",
        "tool_name": "bash",
    }

    submitted = await manager.submit_approval_response(
        session_id,
        "req-runtime-interaction",
        approved=True,
        feedback="approved",
        scope="session",
    )
    waited = await wait_task

    assert submitted is not None
    assert submitted.approved is True
    assert waited.approved is True
    resolved = runtime_store.interactions[0]
    assert resolved.status == "approved"
    assert resolved.response_payload == {
        "session_id": session_id,
        "request_id": "req-runtime-interaction",
        "approved": True,
        "feedback": "approved",
        "scope": "session",
    }
    assert resolved.resolved_at is not None


@pytest.mark.asyncio
async def test_wait_for_http_approval_resolves_runtime_interaction_on_timeout() -> None:
    runtime_store = FakeRuntimeStore()
    manager = SessionManager(runtime_store=runtime_store)
    session_id = await manager.create_session(provider=MockProvider())
    session = manager.get_session(session_id)
    session.turn_in_progress = True
    session.current_turn_id = "run-timeout"
    req = ApprovalRequest(
        session_id=session_id,
        request_id="req-timeout-interaction",
        tool_call=ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={"command": "pwd"},
            call_id="call-timeout-interaction",
        ),
        timeout_seconds=0,
    )

    response = await manager.wait_for_http_approval(
        session_id,
        req,
        timeout_seconds=0,
    )

    assert response.approved is False
    assert response.feedback == "Approval timeout or error"
    assert len(runtime_store.interactions) == 1
    interaction = runtime_store.interactions[0]
    assert interaction.interaction_id == "run-timeout:approval:req-timeout-interaction"
    assert interaction.status == "timed_out"
    assert interaction.response_payload == {
        "session_id": session_id,
        "request_id": "req-timeout-interaction",
        "approved": False,
        "feedback": "Approval timeout or error",
        "scope": "once",
    }


@pytest.mark.asyncio
async def test_wait_for_http_approval_persists_session_auto_approval_interaction() -> (
    None
):
    runtime_store = FakeRuntimeStore()
    manager = SessionManager(runtime_store=runtime_store)
    session_id = await manager.create_session(provider=MockProvider())
    session = manager.get_session(session_id)
    session.turn_in_progress = True
    session.current_turn_id = "run-auto-approval"
    req = ApprovalRequest(
        session_id=session_id,
        request_id="req-auto-interaction",
        tool_call=ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={"command": "pwd"},
            call_id="call-auto-interaction",
        ),
        timeout_seconds=5,
    )
    session.approval_coordinator.remember_session_approval(req)

    response = await manager.wait_for_http_approval(
        session_id,
        req,
        timeout_seconds=5,
    )

    assert response.approved is True
    assert response.scope == "session"
    assert len(runtime_store.interactions) == 1
    interaction = runtime_store.interactions[0]
    assert interaction.interaction_id == (
        "run-auto-approval:approval:req-auto-interaction"
    )
    assert interaction.status == "approved"
    assert interaction.response_payload == {
        "session_id": session_id,
        "request_id": "req-auto-interaction",
        "approved": True,
        "feedback": None,
        "scope": "session",
    }


@pytest.mark.asyncio
async def test_run_agent_persists_message_snapshot_when_runtime_store_configured() -> (
    None
):
    runtime_store = FakeRuntimeStore()
    manager = SessionManager(runtime_store=runtime_store)
    session_id = await manager.create_session()
    message_timestamp = datetime(2026, 1, 2, 3, 4, 5)

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> TurnOutcome:
            del prompt
            self.ctx.messages = [
                {
                    "role": "system",
                    "content": "runtime context",
                    "created_at": message_timestamp,
                },
                {"role": "user", "content": "hello"},
            ]
            return TurnOutcome(stop_reason=StopReason.NO_TOOL_CALLS, steps_taken=1)

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        environment = kwargs["environment"]
        if not isinstance(environment, LocalEnvironment):
            raise TypeError("expected local environment")
        return fake_pipeline, types.SimpleNamespace(
            session_id=kwargs["session_id_override"],
            config={},
            tape=kwargs.get("tape") or Tape(tape_id="stable-tape"),
            run_context=AgentRunContext(
                session_id=kwargs["session_id_override"],
                run_id=kwargs["run_id_override"],
                agent_id=None,
                environment=environment,
            ),
            messages=[],
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "hello")

    assert len(runtime_store.snapshots) == 1
    snapshot = runtime_store.snapshots[0]
    assert snapshot.snapshot_id == f"{runtime_store.created[0].run_id}:latest"
    assert snapshot.run_id == runtime_store.created[0].run_id
    assert snapshot.messages == [
        {
            "role": "system",
            "content": "runtime context",
            "created_at": message_timestamp.isoformat(),
        },
        {"role": "user", "content": "hello"},
    ]
    assert snapshot.metadata == {
        "session_id": session_id,
        "tape_id": "stable-tape",
        "message_count": 2,
        "snapshot_kind": "latest_context",
    }


def test_load_pg_storage_types_reports_missing_optional_dependencies() -> None:
    with pytest.MonkeyPatch.context() as mp:
        fake_import_error = ModuleNotFoundError("No module named 'asyncpg'")
        mp.setattr(
            "coding_agent.server.session_manager.importlib.import_module",
            lambda name: (_ for _ in ()).throw(fake_import_error),
        )

        with pytest.raises(RuntimeError, match="optional dependencies"):
            _load_pg_storage_types()


def test_load_pg_storage_types_reports_missing_exports() -> None:
    fake_module = types.SimpleNamespace(PGPool=object(), PGTapeStore=object())

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "coding_agent.server.session_manager.importlib.import_module",
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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)

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
    observed_run_ids: list[str] = []

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx
            adapter_instances.append(self)

        async def run_turn(self, prompt: str) -> None:
            observed_prompts.append(prompt)
            observed_run_ids.append(self.ctx.run_context.run_id)

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        ),
        _directive_executor=None,
    )

    def fake_create_agent(**kwargs):
        nonlocal create_agent_calls
        create_agent_calls += 1
        environment = kwargs["environment"]
        if not isinstance(environment, LocalEnvironment):
            raise TypeError("expected local environment")
        return fake_pipeline, types.SimpleNamespace(
            session_id=kwargs["session_id_override"],
            config={},
            tape=kwargs.get("tape") or Tape(),
            run_context=AgentRunContext(
                session_id=kwargs["session_id_override"],
                run_id=kwargs["run_id_override"],
                agent_id=None,
                environment=environment,
            ),
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "first")
        await manager.run_agent(session_id, "second")

    assert create_agent_calls == 1
    assert len(adapter_instances) == 1
    assert observed_prompts == ["first", "second"]
    assert len(observed_run_ids) == 2
    assert observed_run_ids[0] != observed_run_ids[1]
    assert manager.get_session(session_id).current_turn_id == observed_run_ids[1]


@pytest.mark.asyncio
async def test_run_agent_rebuilds_live_runtime_when_default_run_target_changes(
    tmp_path: Path,
) -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    first_workspace = tmp_path / "first"
    first_workspace.mkdir()
    second_workspace = tmp_path / "second"
    second_workspace.mkdir()
    session_id = await manager.create_session(
        execution_binding=LocalExecutionBinding(workspace_root=str(first_workspace)),
    )

    create_agent_roots: list[Path] = []
    close_calls: list[str] = []

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt

        async def close(self) -> None:
            close_calls.append(str(self.ctx.run_context.environment.workspace_root))

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        ),
        _directive_executor=None,
    )

    def fake_create_agent(**kwargs):
        environment = kwargs["environment"]
        if not isinstance(environment, LocalEnvironment):
            raise TypeError("expected local environment")
        create_agent_roots.append(environment.workspace_root)
        return fake_pipeline, types.SimpleNamespace(
            session_id=kwargs["session_id_override"],
            config={},
            tape=kwargs.get("tape") or Tape(),
            run_context=AgentRunContext(
                session_id=kwargs["session_id_override"],
                run_id=kwargs["run_id_override"],
                agent_id=None,
                environment=environment,
            ),
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "first")
        session = manager.get_session(session_id)
        session.default_run_target = RunTarget(
            workspace=LocalPathWorkspaceRef(path=str(second_workspace)),
            executor=LocalDaemonExecutorRef(),
            isolation=IsolationPolicy(kind="default_local_sandbox"),
        )
        await manager.run_agent(session_id, "second")

    assert create_agent_roots == [
        first_workspace.resolve(),
        second_workspace.resolve(),
    ]
    assert close_calls == [str(first_workspace.resolve())]


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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
        await manager.run_agent(session_id, "boom")

    session = manager.get_session(session_id)
    assert close_calls == ["closed"]
    assert session.runtime_pipeline is None
    assert session.runtime_ctx is None
    assert session.runtime_adapter is None


@pytest.mark.asyncio
async def test_run_agent_propagates_runtime_close_failure_during_turn_error() -> None:
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
            raise RuntimeError("close exploded")

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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
        with pytest.raises(RuntimeError, match="close exploded"):
            await manager.run_agent(session_id, "boom")

    session = manager.get_session(session_id)
    assert close_calls == ["closed"]
    assert session.turn_in_progress is False


@pytest.mark.asyncio
async def test_run_agent_reraises_owner_conflict_without_sending_error_turn() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)

    close_calls: list[str] = []

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
        with pytest.raises(
            SessionOwnershipConflictError,
            match="stale owner or fencing token rejected",
        ):
            await manager.run_agent(session_id, "boom")

    assert close_calls == ["closed"]
    assert session.runtime_pipeline is None
    assert session.runtime_ctx is None
    assert session.runtime_adapter is None
    assert session.turn_in_progress is False
    assert session.wire.consume_outgoing() is None


@pytest.mark.asyncio
async def test_run_agent_reraises_fatal_tool_execution_error_without_sending_error_turn() -> (
    None
):
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)

    close_calls: list[str] = []

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt
            raise FatalToolExecutionError("fatal tool failure")

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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
        with pytest.raises(FatalToolExecutionError, match="fatal tool failure"):
            await manager.run_agent(session_id, "boom")

    assert close_calls == ["closed"]
    assert session.runtime_pipeline is None
    assert session.runtime_ctx is None
    assert session.runtime_adapter is None
    assert session.turn_in_progress is False
    assert session.wire.consume_outgoing() is None


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
        "coding_agent.server.session_manager.asyncio.wait_for",
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
        "coding_agent.server.session_manager.asyncio.wait_for",
        side_effect=asyncio.TimeoutError,
    ):
        with pytest.raises(RuntimeError, match="did not stop after cancellation"):
            await manager.shutdown_session_runtime(session_id)

    assert manager.has_session(session_id) is True
    assert fake_task.cancel_calls == 1


@pytest.mark.asyncio
async def test_cancel_session_turn_returns_cancelling_for_active_turn() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    task = asyncio.create_task(asyncio.sleep(60))
    session.task = task
    session.turn_in_progress = True

    result = await manager.cancel_session_turn(session_id)

    assert result.session_id == session_id
    assert result.status == "cancelling"
    assert session.as_dict()["turn_status"] == "cancelling"

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_cancel_session_turn_exposes_cancelled_final_state() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    task = asyncio.create_task(asyncio.sleep(60))
    session.task = task
    session.turn_in_progress = True

    await manager.cancel_session_turn(session_id)

    for _ in range(20):
        if session.as_dict()["turn_status"] == "cancelled":
            break
        await asyncio.sleep(0.01)

    assert session.as_dict()["turn_status"] == "cancelled"
    assert session.turn_in_progress is False
    assert session.task is None


@pytest.mark.asyncio
async def test_cancel_session_turn_is_idempotent_for_idle_session() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()

    result = await manager.cancel_session_turn(session_id)

    assert result.session_id == session_id
    assert result.status == "idle"
    assert manager.get_session(session_id).as_dict()["turn_status"] == "idle"


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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)

        first = asyncio.create_task(manager.run_agent(session_id, "first"))
        await asyncio.wait_for(started.wait(), timeout=1)

        with pytest.raises(RuntimeError, match="turn already in progress"):
            await manager.run_agent(session_id, "second")

        release.set()
        await first


@pytest.mark.asyncio
async def test_prepare_session_turn_rejects_active_workspace_export() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session(
        execution_binding=CloudWorkspaceBinding(
            workspace_url="docker://agent-ws-export/workspace",
            workspace_id="ws-export",
        )
    )
    export_started = asyncio.Event()
    release_export = threading.Event()

    def blocking_export(binding: CloudWorkspaceBinding) -> str:
        export_started.set()
        assert release_export.wait(timeout=10)
        return binding.workspace_id

    export_task = asyncio.create_task(
        manager.export_workspace_archive(session_id, blocking_export)
    )
    await asyncio.wait_for(export_started.wait(), timeout=1)

    try:
        with pytest.raises(RuntimeError, match="turn already in progress"):
            await manager.prepare_session_turn(session_id)
    finally:
        release_export.set()
        await export_task


@pytest.mark.asyncio
async def test_run_agent_rejects_active_workspace_export() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session(
        execution_binding=CloudWorkspaceBinding(
            workspace_url="docker://agent-ws-export/workspace",
            workspace_id="ws-export",
        )
    )
    export_started = asyncio.Event()
    release_export = threading.Event()

    def blocking_export(binding: CloudWorkspaceBinding) -> str:
        export_started.set()
        assert release_export.wait(timeout=10)
        return binding.workspace_id

    export_task = asyncio.create_task(
        manager.export_workspace_archive(session_id, blocking_export)
    )
    await asyncio.wait_for(export_started.wait(), timeout=1)

    try:
        with pytest.raises(RuntimeError, match="turn already in progress"):
            await manager.run_agent(session_id, "do work")
    finally:
        release_export.set()
        await export_task


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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(rehydrated, "_restore_tape", fake_restore_tape)

        await rehydrated.run_agent(session_id, "resume")

    assert created_tapes == [persisted_tape]


@pytest.mark.asyncio
async def test_failover_rebuilds_from_persisted_state_without_resuming_local_runtime() -> (
    None
):
    store = InMemorySessionStore()
    initial_ctx = types.SimpleNamespace(
        config={"tool_registry": object()},
        tape=Tape(tape_id="persisted-tape"),
        plugin_states={},
    )
    rebuilt_ctx = types.SimpleNamespace(
        config={"tool_registry": object()},
        tape=Tape(tape_id="persisted-tape"),
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
            return None

    create_calls: list[tuple[str | None, str | None]] = []

    def create_initial_agent(**kwargs):
        tape = kwargs.get("tape")
        create_calls.append(
            (kwargs.get("session_id_override"), None if tape is None else tape.tape_id)
        )
        return fake_pipeline, initial_ctx

    def create_failover_agent(**kwargs):
        tape = kwargs.get("tape")
        create_calls.append(
            (kwargs.get("session_id_override"), None if tape is None else tape.tape_id)
        )
        return fake_pipeline, rebuilt_ctx

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "coding_agent.server.session_manager.PipelineAdapter", FakeAdapter
    )
    original_task: asyncio.Task[object] | None = None

    try:
        first_manager = SessionManager(
            store=store, create_agent_fn=create_initial_agent
        )
        session_id = await first_manager.create_session()
        await first_manager.ensure_session_runtime(session_id)

        original_session = first_manager.get_session(session_id)
        original_session.turn_in_progress = True
        original_task = asyncio.create_task(asyncio.sleep(60))
        original_session.task = original_task
        original_session.event_queues.append(asyncio.Queue())
        original_session.approval_store.add_request(
            ApprovalRequest(
                session_id=session_id,
                request_id="req-local",
                tool_call=ToolCallDelta(
                    session_id=session_id,
                    tool_name="bash",
                    arguments={"command": "pwd"},
                    call_id="call-req-local",
                ),
                timeout_seconds=30,
            )
        )
        original_session.pending_approval = {
            "request_id": "req-local",
            "tool_name": "bash",
        }
        await first_manager._persist_session_async(original_session)

        second_manager = SessionManager(
            store=store, create_agent_fn=create_failover_agent
        )
        reloaded_session = second_manager.get_session(session_id)

        assert reloaded_session.runtime_pipeline is None
        assert reloaded_session.runtime_ctx is None
        assert reloaded_session.runtime_adapter is None
        assert reloaded_session.task is None
        assert reloaded_session.turn_in_progress is False
        assert reloaded_session.event_queues == []
        assert reloaded_session.pending_approval is None
        assert reloaded_session.approval_store.get_request("req-local") is None

        returned_ctx = await second_manager.ensure_session_runtime(session_id)

        assert returned_ctx is rebuilt_ctx
        assert create_calls == [
            (session_id, None),
            (session_id, "persisted-tape"),
        ]
    finally:
        monkeypatch.undo()
        if original_task is not None:
            original_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await original_task


@pytest.mark.asyncio
async def test_ensure_session_runtime_uses_default_run_target_workspace(
    tmp_path: Path,
) -> None:
    store = InMemorySessionStore()
    legacy_bound = tmp_path / "legacy-bound"
    target_bound = tmp_path / "target-bound"
    legacy_bound.mkdir()
    target_bound.mkdir()
    captured_kwargs: dict[str, object] = {}
    local_executor = RecordingLocalDaemonExecutor()
    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        ),
        _directive_executor=None,
    )

    def fake_create_agent(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_pipeline, types.SimpleNamespace(
            config={},
            tape=kwargs.get("tape") or Tape(),
            plugin_states={},
        )

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            return None

    manager = SessionManager(
        store=store,
        create_agent_fn=fake_create_agent,
        local_daemon_executor=local_executor,
    )
    session_id = await manager.create_session(
        execution_binding=LocalExecutionBinding(workspace_root=str(legacy_bound)),
    )
    session = manager.get_session(session_id)
    session.default_run_target = RunTarget(
        workspace=LocalPathWorkspaceRef(path=str(target_bound)),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="default_local_sandbox"),
    )
    manager.register_session(session)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
        await manager.ensure_session_runtime(session_id)

    assert captured_kwargs["workspace_root"] == target_bound.resolve()
    assert isinstance(captured_kwargs["environment"], LocalEnvironment)
    assert captured_kwargs["environment"].workspace_root == target_bound.resolve()
    assert len(local_executor.preparations) == 1
    assert local_executor.preparations[0].request.target == session.default_run_target
    assert isinstance(session.execution_binding, LocalExecutionBinding)
    assert session.execution_binding.workspace_root == str(legacy_bound)


@pytest.mark.asyncio
async def test_replace_session_runtime_config_uses_default_run_target_workspace(
    tmp_path: Path,
) -> None:
    store = InMemorySessionStore()
    legacy_bound = tmp_path / "legacy-bound"
    target_bound = tmp_path / "target-bound"
    legacy_bound.mkdir()
    target_bound.mkdir()
    captured_kwargs: dict[str, object] = {}
    local_executor = RecordingLocalDaemonExecutor()
    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        ),
        _directive_executor=None,
    )

    def fake_create_agent(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_pipeline, types.SimpleNamespace(
            config={},
            tape=kwargs.get("tape") or Tape(),
            plugin_states={},
        )

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            return None

    manager = SessionManager(
        store=store,
        create_agent_fn=fake_create_agent,
        local_daemon_executor=local_executor,
    )
    session_id = await manager.create_session(
        execution_binding=LocalExecutionBinding(workspace_root=str(legacy_bound)),
    )
    session = manager.get_session(session_id)
    session.default_run_target = RunTarget(
        workspace=LocalPathWorkspaceRef(path=str(target_bound)),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="default_local_sandbox"),
    )
    manager.register_session(session)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
        await manager.replace_session_runtime_config(
            session_id,
            model_name="replacement-model",
        )

    assert captured_kwargs["workspace_root"] == target_bound.resolve()
    assert isinstance(captured_kwargs["environment"], LocalEnvironment)
    assert captured_kwargs["environment"].workspace_root == target_bound.resolve()
    assert len(local_executor.preparations) == 1
    assert local_executor.preparations[0].request.target == session.default_run_target
    assert isinstance(session.execution_binding, LocalExecutionBinding)
    assert session.execution_binding.workspace_root == str(legacy_bound)


@pytest.mark.asyncio
async def test_ensure_session_runtime_builds_from_preparation_target(
    tmp_path: Path,
) -> None:
    store = InMemorySessionStore()
    original_bound = tmp_path / "original-bound"
    mutated_bound = tmp_path / "mutated-bound"
    original_bound.mkdir()
    mutated_bound.mkdir()
    captured_kwargs: dict[str, object] = {}
    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        ),
        _directive_executor=None,
    )

    def fake_create_agent(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_pipeline, types.SimpleNamespace(
            config={},
            tape=kwargs.get("tape") or Tape(),
            plugin_states={},
        )

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            return None

    mutated_target = RunTarget(
        workspace=LocalPathWorkspaceRef(path=str(mutated_bound)),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="default_local_sandbox"),
    )

    class MutatingPrepareExecutor(LocalDaemonExecutor):
        def __init__(self) -> None:
            self.preparations: list[LocalDaemonRuntimePreparation] = []
            self.session: Any | None = None

        async def prepare_runtime(
            self,
            preparation: LocalDaemonRuntimePreparation,
        ) -> LocalDaemonRuntimeBinding:
            self._validate_request_target(preparation.request)
            self.preparations.append(preparation)
            if self.session is None:
                raise AssertionError("session must be assigned before preparation")
            self.session.default_run_target = mutated_target
            return await preparation.runtime_provider.prepare_runtime(
                preparation.request
            )

    local_executor = MutatingPrepareExecutor()
    manager = SessionManager(
        store=store,
        create_agent_fn=fake_create_agent,
        local_daemon_executor=local_executor,
    )
    session_id = await manager.create_session(
        execution_binding=LocalExecutionBinding(workspace_root=str(original_bound)),
    )
    session = manager.get_session(session_id)
    original_target = RunTarget(
        workspace=LocalPathWorkspaceRef(path=str(original_bound)),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="default_local_sandbox"),
    )
    session.default_run_target = original_target
    local_executor.session = session
    manager.register_session(session)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
        await manager.ensure_session_runtime(session_id)

    assert len(local_executor.preparations) == 1
    assert local_executor.preparations[0].request.target == original_target
    assert captured_kwargs["workspace_root"] == original_bound.resolve()
    assert isinstance(captured_kwargs["environment"], LocalEnvironment)
    assert captured_kwargs["environment"].workspace_root == original_bound.resolve()


@pytest.mark.asyncio
async def test_ensure_session_runtime_rejects_local_daemon_non_local_workspace() -> None:
    def fake_create_agent(**kwargs):
        del kwargs
        raise AssertionError("agent builder should not run for invalid target")

    manager = SessionManager(
        store=InMemorySessionStore(),
        create_agent_fn=fake_create_agent,
    )
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.default_run_target = RunTarget(
        workspace=CloudWorkspaceRef(
            workspace_url="docker://workspace/ws-1",
            workspace_id="ws-1",
        ),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="provider_sandbox"),
    )
    manager.register_session(session)

    with pytest.raises(
        RunExecutorTargetError,
        match="LocalDaemonExecutor requires a local_path workspace target",
    ):
        await manager.ensure_session_runtime(session_id)


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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
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
async def test_run_agent_uses_resolved_workspace_root_from_binding(
    tmp_path: Path,
) -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.repo_path = tmp_path / "not-used-directly"
    bound_workspace = tmp_path / "bound-workspace"
    session.execution_binding = LocalExecutionBinding(
        workspace_root=str(bound_workspace)
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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
        await manager.run_agent(session_id, "hello")

    assert captured_kwargs["workspace_root"] == bound_workspace.resolve()
    assert isinstance(captured_kwargs["environment"], LocalEnvironment)
    assert captured_kwargs["environment"].workspace_root == bound_workspace.resolve()


@pytest.mark.asyncio
async def test_restore_checkpoint_preserves_execution_binding(tmp_path: Path) -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.tape_id = "binding-restore-tape"
    restore_bound = tmp_path / "restore-bound"
    session.execution_binding = LocalExecutionBinding(workspace_root=str(restore_bound))
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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        await manager._restore_checkpoint(session, "cp-binding")

    assert captured_kwargs["workspace_root"] == restore_bound.resolve()
    assert isinstance(captured_kwargs["environment"], LocalEnvironment)
    assert captured_kwargs["environment"].workspace_root == restore_bound.resolve()
    assert isinstance(session.execution_binding, LocalExecutionBinding)
    assert session.execution_binding.workspace_root == str(restore_bound)


@pytest.mark.asyncio
async def test_restore_checkpoint_uses_default_run_target_workspace(
    tmp_path: Path,
) -> None:
    store = InMemorySessionStore()
    local_executor = RecordingLocalDaemonExecutor()
    manager = SessionManager(store=store, local_daemon_executor=local_executor)
    legacy_bound = tmp_path / "legacy-bound"
    target_bound = tmp_path / "target-bound"
    legacy_bound.mkdir()
    target_bound.mkdir()
    session_id = await manager.create_session(
        execution_binding=LocalExecutionBinding(workspace_root=str(legacy_bound)),
    )
    session = manager.get_session(session_id)
    session.tape_id = "target-restore-tape"
    session.default_run_target = RunTarget(
        workspace=LocalPathWorkspaceRef(path=str(target_bound)),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="default_local_sandbox"),
    )
    manager.register_session(session)

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-target-binding",
            tape_id="target-restore-tape",
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
            assert checkpoint_id == "cp-target-binding"
            return snapshot

        async def list(self, tape_id: str):
            assert tape_id == "target-restore-tape"
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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        await manager._restore_checkpoint(session, "cp-target-binding")

    assert captured_kwargs["workspace_root"] == target_bound.resolve()
    assert isinstance(captured_kwargs["environment"], LocalEnvironment)
    assert captured_kwargs["environment"].workspace_root == target_bound.resolve()
    assert len(local_executor.preparations) == 1
    assert local_executor.preparations[0].request.target == session.default_run_target
    assert isinstance(session.execution_binding, LocalExecutionBinding)
    assert session.execution_binding.workspace_root == str(legacy_bound)


@pytest.mark.asyncio
async def test_restore_checkpoint_builds_from_preparation_target(
    tmp_path: Path,
) -> None:
    store = InMemorySessionStore()
    original_bound = tmp_path / "original-bound"
    mutated_bound = tmp_path / "mutated-bound"
    original_bound.mkdir()
    mutated_bound.mkdir()
    captured_kwargs: dict[str, object] = {}
    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-preparation-target",
            tape_id="restore-preparation-tape",
            entry_count=0,
            window_start=0,
        ),
        tape_entries=(),
        plugin_states={},
        extra={},
    )

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-preparation-target"
            return snapshot

        async def list(self, tape_id: str):
            assert tape_id == "restore-preparation-tape"
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

    mutated_target = RunTarget(
        workspace=LocalPathWorkspaceRef(path=str(mutated_bound)),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="default_local_sandbox"),
    )

    class MutatingPrepareExecutor(LocalDaemonExecutor):
        def __init__(self) -> None:
            self.preparations: list[LocalDaemonRuntimePreparation] = []
            self.session: Any | None = None

        async def prepare_runtime(
            self,
            preparation: LocalDaemonRuntimePreparation,
        ) -> LocalDaemonRuntimeBinding:
            self._validate_request_target(preparation.request)
            self.preparations.append(preparation)
            if self.session is None:
                raise AssertionError("session must be assigned before preparation")
            self.session.default_run_target = mutated_target
            return await preparation.runtime_provider.prepare_runtime(
                preparation.request
            )

    local_executor = MutatingPrepareExecutor()
    manager = SessionManager(
        store=store,
        create_agent_fn=fake_create_agent,
        local_daemon_executor=local_executor,
    )
    session_id = await manager.create_session(
        execution_binding=LocalExecutionBinding(workspace_root=str(original_bound)),
    )
    session = manager.get_session(session_id)
    session.tape_id = "restore-preparation-tape"
    original_target = RunTarget(
        workspace=LocalPathWorkspaceRef(path=str(original_bound)),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="default_local_sandbox"),
    )
    session.default_run_target = original_target
    local_executor.session = session
    manager.register_session(session)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        await manager._restore_checkpoint(session, "cp-preparation-target")

    assert len(local_executor.preparations) == 1
    assert local_executor.preparations[0].request.target == original_target
    assert captured_kwargs["workspace_root"] == original_bound.resolve()
    assert isinstance(captured_kwargs["environment"], LocalEnvironment)
    assert captured_kwargs["environment"].workspace_root == original_bound.resolve()


@pytest.mark.asyncio
async def test_restore_checkpoint_rejects_local_daemon_non_local_workspace() -> None:
    def fake_create_agent(**kwargs):
        del kwargs
        raise AssertionError("agent builder should not run for invalid target")

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-invalid-target",
            tape_id="invalid-target-tape",
            entry_count=0,
            window_start=0,
        ),
        tape_entries=(),
        plugin_states={},
        extra={},
    )

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-invalid-target"
            return snapshot

        async def list(self, tape_id: str):
            assert tape_id == "invalid-target-tape"
            return [snapshot.meta]

        async def delete(self, checkpoint_id: str) -> None:
            return None

    manager = SessionManager(
        store=InMemorySessionStore(),
        create_agent_fn=fake_create_agent,
    )
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.tape_id = "invalid-target-tape"
    session.default_run_target = RunTarget(
        workspace=CloudWorkspaceRef(
            workspace_url="docker://workspace/ws-1",
            workspace_id="ws-1",
        ),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="provider_sandbox"),
    )
    manager.register_session(session)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        with pytest.raises(
            RunExecutorTargetError,
            match="LocalDaemonExecutor requires a local_path workspace target",
        ):
            await manager._restore_checkpoint(session, "cp-invalid-target")


class FakeCloudClient:
    workspace_id = "ws-123"
    workspace_url = "https://workspace.example.com"
    default_cwd = "/workspace"

    def read_file(self, path: str) -> str:
        return path

    def write_file(self, path: str, content: str) -> None:
        del path, content

    def replace_file(self, path: str, old: str, new: str) -> None:
        del path, old, new

    def glob_files(self, pattern: str, directory: str) -> list[str]:
        del pattern, directory
        return []

    def grep_search(self, pattern: str, directory: str, include: str) -> list[str]:
        del pattern, directory, include
        return []

    def apply_patch(self, path: str, patch: str) -> dict[str, object]:
        del patch
        return {"success": True, "path": path, "changed": False}

    def run_command(
        self,
        command: str,
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        timeout: int,
    ) -> CloudCommandResult:
        del command, cwd, env, timeout
        return CloudCommandResult(stdout="", stderr="", exit_code=0)


@pytest.mark.asyncio
async def test_run_agent_does_not_bootstrap_cloud_runtime_from_execution_binding() -> (
    None
):
    runtime_store = FakeRuntimeStore()

    def fake_create_agent(**kwargs):
        raise AssertionError(f"cloud runtime must not be bootstrapped: {kwargs!r}")

    manager = SessionManager(
        store=InMemorySessionStore(),
        runtime_store=runtime_store,
        create_agent_fn=fake_create_agent,
        binding_resolver=DefaultBindingResolver(
            cloud_client_factory=lambda binding: FakeCloudClient()
        ),
    )
    session_id = await manager.create_session(
        execution_binding=CloudWorkspaceBinding(
            workspace_url="https://workspace.example.com",
            workspace_id="ws-123",
        )
    )

    await manager.run_agent(session_id, "hello")

    assert runtime_store.created[0].metadata["workspace_surface"] == "cloud_workspace"
    assert runtime_store.created[0].metadata["execution_plane"] == "control_plane"
    assert runtime_store.updated[0]["status"] == "running"
    assert runtime_store.updated[-1]["status"] == "failed"
    assert runtime_store.updated[-1]["error"] is not None
    assert "managed_pool" in str(runtime_store.updated[-1]["error"])


@pytest.mark.asyncio
async def test_run_agent_routes_unsupported_runtime_through_run_coordinator() -> None:
    runtime_store = FakeRuntimeStore()
    coordinator = RejectingRunCoordinator(
        error="managed_pool runtime execution is not available through this coordinator"
    )

    def fake_create_agent(**kwargs):
        raise AssertionError(f"cloud runtime must not be bootstrapped: {kwargs!r}")

    manager = SessionManager(
        store=InMemorySessionStore(),
        runtime_store=runtime_store,
        create_agent_fn=fake_create_agent,
        run_coordinator=coordinator,
        binding_resolver=DefaultBindingResolver(
            cloud_client_factory=lambda binding: FakeCloudClient()
        ),
    )
    session_id = await manager.create_session(
        execution_binding=CloudWorkspaceBinding(
            workspace_url="https://workspace.example.com",
            workspace_id="ws-123",
        )
    )

    await manager.run_agent(session_id, "hello")

    assert len(coordinator.requests) == 1
    assert len(coordinator.executions) == 1
    assert isinstance(coordinator.executions[0], LocalDaemonRuntimeExecution)
    assert coordinator.executions[0].request == coordinator.requests[0]
    assert isinstance(coordinator.executions[0].request.target.executor, ManagedPoolExecutorRef)
    assert runtime_store.created[0].metadata["workspace_surface"] == "cloud_workspace"
    assert runtime_store.updated[0]["status"] == "running"
    assert runtime_store.updated[-1]["status"] == "failed"
    assert runtime_store.updated[-1]["error"] == coordinator.error


@pytest.mark.asyncio
async def test_run_agent_does_not_route_cloud_runtime_through_local_daemon_executor() -> None:
    local_executor = RecordingLocalDaemonExecutor()
    runtime_store = FakeRuntimeStore()
    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        environment = kwargs["environment"]
        if not isinstance(environment, CloudEnvironment):
            raise TypeError("expected cloud environment")
        return fake_pipeline, types.SimpleNamespace(
            session_id=kwargs["session_id_override"],
            config={},
            tape=kwargs.get("tape") or Tape(tape_id="stable-tape"),
            run_context=AgentRunContext(
                session_id=kwargs["session_id_override"],
                run_id=kwargs["run_id_override"],
                agent_id=None,
                environment=environment,
            ),
        )

    manager = SessionManager(
        store=InMemorySessionStore(),
        runtime_store=cast(Any, runtime_store),
        create_agent_fn=fake_create_agent,
        local_daemon_executor=local_executor,
        binding_resolver=DefaultBindingResolver(
            cloud_client_factory=lambda binding: FakeCloudClient()
        ),
    )
    session_id = await manager.create_session(
        execution_binding=CloudWorkspaceBinding(
            workspace_url="https://workspace.example.com",
            workspace_id="ws-123",
        )
    )

    class FakeAdapter:
        prompts: list[str] = []

        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> TurnOutcome:
            self.prompts.append(prompt)
            return TurnOutcome(
                stop_reason=StopReason.NO_TOOL_CALLS,
                steps_taken=1,
            )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "hello cloud")

    assert local_executor.executions == []
    assert FakeAdapter.prompts == []
    assert runtime_store.created[0].metadata["workspace_surface"] == "cloud_workspace"
    assert runtime_store.updated[0]["status"] == "running"
    assert runtime_store.updated[-1]["status"] == "failed"
    assert runtime_store.updated[-1]["error"] is not None
    assert "managed_pool" in str(runtime_store.updated[-1]["error"])


@pytest.mark.asyncio
async def test_restore_checkpoint_preserves_cloud_execution_binding() -> None:
    captured_kwargs: dict[str, object] = {}
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

    manager = SessionManager(
        store=InMemorySessionStore(),
        create_agent_fn=fake_create_agent,
        binding_resolver=DefaultBindingResolver(
            cloud_client_factory=lambda binding: FakeCloudClient()
        ),
    )
    session_id = await manager.create_session(
        execution_binding=CloudWorkspaceBinding(
            workspace_url="https://workspace.example.com",
            workspace_id="ws-123",
        )
    )
    session = manager.get_session(session_id)
    session.tape_id = "cloud-restore-tape"
    manager.register_session(session)

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-cloud-binding",
            tape_id="cloud-restore-tape",
            entry_count=0,
            window_start=0,
        ),
        tape_entries=(),
        plugin_states={},
        extra={},
    )

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-cloud-binding"
            return snapshot

        async def list(self, tape_id: str):
            assert tape_id == "cloud-restore-tape"
            return [snapshot.meta]

        async def delete(self, checkpoint_id: str) -> None:
            del checkpoint_id

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            del tape_id, keep

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            return None

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        await manager._restore_checkpoint(session, "cp-cloud-binding")

    assert captured_kwargs["workspace_root"] is None
    assert isinstance(captured_kwargs["environment"], CloudEnvironment)
    assert isinstance(session.execution_binding, CloudWorkspaceBinding)
    assert session.execution_binding.workspace_id == "ws-123"


@pytest.mark.asyncio
async def test_restore_checkpoint_preserves_cloud_run_target_metadata() -> None:
    observed_bindings: list[CloudWorkspaceBinding] = []

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape"), plugin_states={}
        )

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    manager = SessionManager(
        store=InMemorySessionStore(),
        create_agent_fn=fake_create_agent,
        binding_resolver=DefaultBindingResolver(
            cloud_client_factory=lambda binding: (
                observed_bindings.append(binding) or FakeCloudClient()
            )
        ),
    )
    session_id = await manager.create_session(
        execution_binding=CloudWorkspaceBinding(
            workspace_url="https://legacy.example.com",
            workspace_id="legacy-ws",
        )
    )
    session = manager.get_session(session_id)
    session.tape_id = "cloud-target-restore-tape"
    session.default_run_target = RunTarget(
        workspace=CloudWorkspaceRef(
            workspace_url="https://target.example.com",
            workspace_id="target-ws",
            runtime_profile="gpu-large",
            workspace_provider="docker",
            provider_instance_id="docker-host-a",
        ),
        executor=ManagedPoolExecutorRef(pool="gpu"),
        isolation=IsolationPolicy(kind="provider_sandbox"),
    )
    manager.register_session(session)

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-cloud-target",
            tape_id="cloud-target-restore-tape",
            entry_count=0,
            window_start=0,
        ),
        tape_entries=(),
        plugin_states={},
        extra={},
    )

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-cloud-target"
            return snapshot

        async def list(self, tape_id: str):
            assert tape_id == "cloud-target-restore-tape"
            return [snapshot.meta]

        async def delete(self, checkpoint_id: str) -> None:
            del checkpoint_id

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            del tape_id, keep

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            return None

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        await manager._restore_checkpoint(session, "cp-cloud-target")

    assert observed_bindings == [
        CloudWorkspaceBinding(
            workspace_url="https://target.example.com",
            workspace_id="target-ws",
            runtime_profile="gpu-large",
            workspace_provider="docker",
            provider_instance_id="docker-host-a",
        )
    ]


@pytest.mark.asyncio
async def test_close_session_cleans_up_server_provisioned_cloud_binding() -> None:
    cleaned: list[CloudWorkspaceBinding] = []
    manager = SessionManager(
        store=InMemorySessionStore(),
        provisioned_cloud_binding_cleanup=cleaned.append,
    )
    binding = CloudWorkspaceBinding(
        workspace_url="docker://agent-ws-123/workspace",
        workspace_id="ws-123",
    )
    session_id = await manager.create_session(
        execution_binding=binding,
        origin={
            "channel": "http",
            "binding_kind": "cloud",
            "workspace_source_kind": "docker",
        },
    )

    await manager.close_session(session_id)

    assert cleaned == [binding]


@pytest.mark.asyncio
async def test_close_session_offloads_provisioned_cloud_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = SessionManager(
        store=InMemorySessionStore(),
        provisioned_cloud_binding_cleanup=lambda binding: None,
    )
    binding = CloudWorkspaceBinding(
        workspace_url="docker://agent-ws-threaded/workspace",
        workspace_id="ws-threaded",
    )
    session_id = await manager.create_session(
        execution_binding=binding,
        origin={
            "channel": "http",
            "binding_kind": "cloud",
            "workspace_source_kind": "docker",
        },
    )
    session = manager.get_session(session_id)
    to_thread_calls: list[tuple[object, tuple[object, ...]]] = []

    async def fake_to_thread(func, *args):
        to_thread_calls.append((func, args))
        return func(*args)

    monkeypatch.setattr(
        "coding_agent.server.session_manager.asyncio.to_thread", fake_to_thread
    )

    await manager.close_session(session_id)

    assert to_thread_calls == [(manager._cleanup_provisioned_cloud_binding, (session,))]


@pytest.mark.asyncio
async def test_close_session_logs_provisioned_cloud_cleanup_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_cleanup(binding: CloudWorkspaceBinding) -> None:
        del binding
        raise RuntimeError("cleanup failed")

    manager = SessionManager(
        store=InMemorySessionStore(),
        provisioned_cloud_binding_cleanup=fail_cleanup,
    )
    session_id = await manager.create_session(
        execution_binding=CloudWorkspaceBinding(
            workspace_url="docker://agent-ws-fails/workspace",
            workspace_id="ws-fails",
        ),
        origin={
            "channel": "http",
            "binding_kind": "cloud",
            "workspace_source_kind": "docker",
        },
    )

    with caplog.at_level("ERROR"):
        await manager.close_session(session_id)

    assert not manager.has_session(session_id)
    assert "Failed to clean up provisioned cloud workspace" in caplog.text


@pytest.mark.asyncio
async def test_close_session_keeps_explicit_cloud_binding_untouched() -> None:
    cleaned: list[CloudWorkspaceBinding] = []
    manager = SessionManager(
        store=InMemorySessionStore(),
        provisioned_cloud_binding_cleanup=cleaned.append,
    )
    session_id = await manager.create_session(
        execution_binding=CloudWorkspaceBinding(
            workspace_url="https://workspace.example.com",
            workspace_id="ws-explicit",
        ),
        origin={"channel": "http", "binding_kind": "cloud"},
    )

    await manager.close_session(session_id)

    assert cleaned == []


class FakeWorkspaceMetadataStore:
    def __init__(self, records: list[WorkspaceRecord]) -> None:
        self.records = records
        self.status_updates: list[tuple[str, WorkspaceStatus, str | None]] = []

    async def save(self, record: WorkspaceRecord) -> None:
        self.records.append(record)

    async def list(self) -> list[WorkspaceRecord]:
        return self.records

    async def load_by_workspace_id(self, workspace_id: str) -> WorkspaceRecord | None:
        for record in self.records:
            if record.workspace_id == workspace_id:
                return record
        return None

    async def load_for_session_workspace(
        self,
        *,
        session_id: str,
        workspace_id: str,
    ) -> WorkspaceRecord | None:
        for record in self.records:
            if record.session_id == session_id and record.workspace_id == workspace_id:
                return record
        return None

    async def update_status(
        self,
        workspace_record_id: str,
        *,
        status: WorkspaceStatus,
        cleanup_error: str | None = None,
    ) -> None:
        self.status_updates.append((workspace_record_id, status, cleanup_error))

    async def update_retention(
        self,
        workspace_record_id: str,
        *,
        retention_policy: WorkspaceRetentionPolicy,
        expires_at: datetime | None,
        status: WorkspaceStatus,
    ) -> None:
        del workspace_record_id, retention_policy, expires_at, status

    async def update_result_refs(
        self,
        workspace_record_id: str,
        *,
        result_refs: dict[str, JSONValue],
    ) -> None:
        del workspace_record_id, result_refs


def _workspace_record(
    *,
    session_id: str,
    workspace_id: str,
    retention_policy: WorkspaceRetentionPolicy,
) -> WorkspaceRecord:
    return WorkspaceRecord(
        workspace_record_id=f"record-{workspace_id}",
        workspace_id=workspace_id,
        session_id=session_id,
        provider="docker",
        provider_instance_id="docker-host-a",
        workspace_root_ref="/workspaces",
        workspace_host_label="docker-host-a",
        owner_label="owner:test",
        source_kind="git",
        status="active",
        retention_policy=retention_policy,
    )


@pytest.mark.asyncio
async def test_create_session_persists_cloud_workspace_record() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="docker://agent-ws-created/workspace",
        workspace_id="ws-created",
        runtime_profile="universal",
    )
    store = FakeWorkspaceMetadataStore([])
    manager = SessionManager(
        store=InMemorySessionStore(),
        workspace_metadata_store=store,
    )

    session_id = await manager.create_session(
        execution_binding=binding,
        origin={
            "channel": "http",
            "binding_kind": "cloud",
            "workspace_source_kind": "docker",
            "workspace_provider": "docker",
            "provider_instance_id": "docker-host-a",
            "workspace_root_ref": "/workspaces",
            "workspace_host_label": "docker-host-a",
            "owner_label": "owner:test",
        },
    )

    assert store.records == [
        WorkspaceRecord(
            workspace_record_id=f"{session_id}:ws-created",
            workspace_id="ws-created",
            session_id=session_id,
            provider="docker",
            provider_instance_id="docker-host-a",
            workspace_root_ref="/workspaces",
            workspace_host_label="docker-host-a",
            owner_label="owner:test",
            source_kind="docker",
            source_ref={"runtime_profile": "universal"},
            status="active",
            retention_policy="delete_on_close",
        )
    ]


@pytest.mark.asyncio
async def test_create_session_does_not_persist_explicit_cloud_binding_record() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="docker://external-workspace/workspace",
        workspace_id="external-workspace",
    )
    store = FakeWorkspaceMetadataStore([])
    manager = SessionManager(
        store=InMemorySessionStore(),
        workspace_metadata_store=store,
    )

    await manager.create_session(
        execution_binding=binding,
        origin={
            "channel": "http",
            "binding_kind": "cloud",
        },
    )

    assert store.records == []


@pytest.mark.asyncio
async def test_close_session_retains_workspace_when_policy_is_pinned() -> None:
    cleaned: list[CloudWorkspaceBinding] = []
    binding = CloudWorkspaceBinding(
        workspace_url="docker://agent-ws-pinned/workspace",
        workspace_id="ws-pinned",
    )
    store = FakeWorkspaceMetadataStore([])
    manager = SessionManager(
        store=InMemorySessionStore(),
        provisioned_cloud_binding_cleanup=cleaned.append,
        workspace_metadata_store=store,
    )
    session_id = await manager.create_session(
        execution_binding=binding,
        origin={
            "channel": "http",
            "binding_kind": "cloud",
            "workspace_source_kind": "git",
            "workspace_provider": "docker",
            "provider_instance_id": "docker-host-a",
            "workspace_root_ref": "/workspaces",
            "workspace_host_label": "docker-host-a",
            "owner_label": "owner:test",
        },
    )
    store.records[0] = _workspace_record(
        session_id=session_id,
        workspace_id=binding.workspace_id,
        retention_policy="pinned",
    )

    await manager.close_session(session_id)

    assert cleaned == []
    assert store.status_updates == [("record-ws-pinned", "retained", None)]


@pytest.mark.asyncio
async def test_close_session_deletes_workspace_when_policy_is_delete_on_close() -> None:
    cleaned: list[CloudWorkspaceBinding] = []
    binding = CloudWorkspaceBinding(
        workspace_url="docker://agent-ws-delete/workspace",
        workspace_id="ws-delete",
    )
    store = FakeWorkspaceMetadataStore([])
    manager = SessionManager(
        store=InMemorySessionStore(),
        provisioned_cloud_binding_cleanup=cleaned.append,
        workspace_metadata_store=store,
    )
    session_id = await manager.create_session(
        execution_binding=binding,
        origin={
            "channel": "http",
            "binding_kind": "cloud",
            "workspace_source_kind": "git",
            "workspace_provider": "docker",
            "provider_instance_id": "docker-host-a",
            "workspace_root_ref": "/workspaces",
            "workspace_host_label": "docker-host-a",
            "owner_label": "owner:test",
        },
    )
    store.records[0] = _workspace_record(
        session_id=session_id,
        workspace_id=binding.workspace_id,
        retention_policy="delete_on_close",
    )

    await manager.close_session(session_id)

    assert cleaned == [binding]
    assert store.status_updates == [("record-ws-delete", "cleaned", None)]


@pytest.mark.asyncio
async def test_close_session_marks_workspace_cleanup_failed() -> None:
    def fail_cleanup(binding: CloudWorkspaceBinding) -> None:
        del binding
        raise RuntimeError("cleanup failed")

    binding = CloudWorkspaceBinding(
        workspace_url="docker://agent-ws-cleanup-failed/workspace",
        workspace_id="ws-cleanup-failed",
    )
    store = FakeWorkspaceMetadataStore([])
    manager = SessionManager(
        store=InMemorySessionStore(),
        provisioned_cloud_binding_cleanup=fail_cleanup,
        workspace_metadata_store=store,
    )
    session_id = await manager.create_session(
        execution_binding=binding,
        origin={
            "channel": "http",
            "binding_kind": "cloud",
            "workspace_source_kind": "git",
            "workspace_provider": "docker",
            "provider_instance_id": "docker-host-a",
            "workspace_root_ref": "/workspaces",
            "workspace_host_label": "docker-host-a",
            "owner_label": "owner:test",
        },
    )
    store.records[0] = _workspace_record(
        session_id=session_id,
        workspace_id=binding.workspace_id,
        retention_policy="delete_on_close",
    )

    await manager.close_session(session_id)

    assert store.status_updates == [
        (
            "record-ws-cleanup-failed",
            "cleanup_failed",
            "cleanup failed",
        )
    ]


@pytest.mark.asyncio
async def test_shutdown_session_runtime_does_not_apply_workspace_retention() -> None:
    cleaned: list[CloudWorkspaceBinding] = []
    binding = CloudWorkspaceBinding(
        workspace_url="docker://agent-ws-shutdown/workspace",
        workspace_id="ws-shutdown",
    )
    store = FakeWorkspaceMetadataStore([])
    manager = SessionManager(
        store=InMemorySessionStore(),
        provisioned_cloud_binding_cleanup=cleaned.append,
        workspace_metadata_store=store,
    )
    session_id = await manager.create_session(
        execution_binding=binding,
        origin={
            "channel": "http",
            "binding_kind": "cloud",
            "workspace_source_kind": "git",
            "workspace_provider": "docker",
            "provider_instance_id": "docker-host-a",
            "workspace_root_ref": "/workspaces",
            "workspace_host_label": "docker-host-a",
            "owner_label": "owner:test",
        },
    )
    store.records[0] = _workspace_record(
        session_id=session_id,
        workspace_id=binding.workspace_id,
        retention_policy="delete_on_close",
    )

    await manager.shutdown_session_runtime(session_id)

    assert cleaned == []
    assert store.status_updates == []
    assert manager.has_session(session_id)


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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
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
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", NewAdapter)
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
