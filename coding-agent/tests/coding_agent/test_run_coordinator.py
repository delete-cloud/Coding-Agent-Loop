from __future__ import annotations

import pytest

from coding_agent.runs import (
    CloudWorkspaceRef,
    DefaultRunCoordinator,
    IsolationPolicy,
    LocalDaemonExecutorRef,
    LocalPathWorkspaceRef,
    ManagedPoolExecutorRef,
    RunCoordinator,
    RunCoordinatorError,
    RunRequest,
    RunSubmission,
    RunTarget,
)


class RecordingRunExecutor:
    def __init__(self) -> None:
        self.requests: list[RunRequest] = []
        self.executions: list[object] = []

    async def submit_run(self, request: RunRequest) -> RunSubmission:
        self.requests.append(request)
        return RunSubmission(
            session_id=request.session_id,
            run_id=request.run_id,
            target=request.target,
            executor=request.target.executor,
            metadata={"delegated": "local_daemon"},
        )

    async def execute_runtime(self, execution: object) -> object:
        self.executions.append(execution)
        return {"delegated": "local_runtime"}


class RuntimeExecutionStub:
    def __init__(self, request: RunRequest) -> None:
        self.request = request


def _local_target() -> RunTarget:
    return RunTarget(
        workspace=LocalPathWorkspaceRef(path="/repo"),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="default_local_sandbox"),
    )


async def _submit_through_protocol(
    coordinator: RunCoordinator,
    request: RunRequest,
) -> RunSubmission:
    return await coordinator.submit_run(request)


async def _execute_through_protocol(
    coordinator: RunCoordinator,
    execution: RuntimeExecutionStub,
) -> object:
    return await coordinator.execute_runtime(execution)


@pytest.mark.asyncio
async def test_default_run_coordinator_satisfies_submit_protocol() -> None:
    target = _local_target()
    request = RunRequest(session_id="session-1", run_id="run-1", target=target)

    submission = await _submit_through_protocol(DefaultRunCoordinator(), request)

    assert submission.executor is target.executor
    assert submission.status == "accepted"


@pytest.mark.asyncio
async def test_default_run_coordinator_satisfies_runtime_protocol() -> None:
    executor = RecordingRunExecutor()
    target = _local_target()
    request = RunRequest(session_id="session-1", run_id="run-1", target=target)
    execution = RuntimeExecutionStub(request)

    result = await _execute_through_protocol(
        DefaultRunCoordinator(local_daemon_executor=executor),
        execution,
    )

    assert result == {"delegated": "local_runtime"}
    assert executor.executions == [execution]


@pytest.mark.asyncio
async def test_default_run_coordinator_selects_executor_from_target() -> None:
    target = _local_target()
    request = RunRequest(
        session_id="session-1",
        run_id="run-1",
        target=target,
        input_summary="implement the task",
        metadata={"source": "test"},
    )

    submission = await DefaultRunCoordinator().submit_run(request)

    assert isinstance(submission, RunSubmission)
    assert submission.status == "accepted"
    assert submission.session_id == "session-1"
    assert submission.run_id == "run-1"
    assert submission.target is target
    assert submission.executor is target.executor
    assert isinstance(submission.executor, LocalDaemonExecutorRef)
    assert submission.metadata == {"source": "test"}


@pytest.mark.asyncio
async def test_default_run_coordinator_preserves_managed_pool_executor() -> None:
    target = RunTarget(
        workspace=CloudWorkspaceRef(
            workspace_url="docker://workspace/ws-1",
            workspace_id="ws-1",
        ),
        executor=ManagedPoolExecutorRef(pool="cloud"),
        isolation=IsolationPolicy(
            kind="provider_sandbox",
            network="provider_managed",
            filesystem="provider_managed",
            secrets="provider_managed",
        ),
    )
    request = RunRequest(session_id="session-1", run_id="run-1", target=target)

    submission = await DefaultRunCoordinator().submit_run(request)

    assert isinstance(submission.executor, ManagedPoolExecutorRef)
    assert submission.executor.pool == "cloud"
    assert submission.target.workspace is target.workspace


@pytest.mark.asyncio
async def test_default_run_coordinator_does_not_delegate_managed_pool_target() -> None:
    executor = RecordingRunExecutor()
    target = RunTarget(
        workspace=CloudWorkspaceRef(
            workspace_url="docker://workspace/ws-1",
            workspace_id="ws-1",
        ),
        executor=ManagedPoolExecutorRef(pool="cloud"),
        isolation=IsolationPolicy(
            kind="provider_sandbox",
            network="provider_managed",
            filesystem="provider_managed",
            secrets="provider_managed",
        ),
    )
    request = RunRequest(session_id="session-1", run_id="run-1", target=target)

    submission = await DefaultRunCoordinator(
        local_daemon_executor=executor,
    ).submit_run(request)

    assert executor.requests == []
    assert isinstance(submission.executor, ManagedPoolExecutorRef)
    assert submission.executor.pool == "cloud"
    assert submission.target.workspace is target.workspace


@pytest.mark.asyncio
async def test_default_run_coordinator_delegates_local_daemon_executor() -> None:
    executor = RecordingRunExecutor()
    target = _local_target()
    request = RunRequest(session_id="session-1", run_id="run-1", target=target)

    submission = await DefaultRunCoordinator(
        local_daemon_executor=executor,
    ).submit_run(request)

    assert executor.requests == [request]
    assert submission.metadata == {"delegated": "local_daemon"}


@pytest.mark.asyncio
async def test_default_run_coordinator_delegates_local_runtime_execution() -> None:
    executor = RecordingRunExecutor()
    target = _local_target()
    request = RunRequest(session_id="session-1", run_id="run-1", target=target)
    execution = RuntimeExecutionStub(request)

    result = await DefaultRunCoordinator(
        local_daemon_executor=executor,
    ).execute_runtime(execution)

    assert result == {"delegated": "local_runtime"}
    assert executor.executions == [execution]


@pytest.mark.asyncio
async def test_default_run_coordinator_rejects_missing_local_runtime_executor() -> None:
    request = RunRequest(
        session_id="session-1",
        run_id="run-1",
        target=_local_target(),
    )

    with pytest.raises(
        RunCoordinatorError,
        match="local_daemon runtime executor is not configured",
    ):
        _ = await DefaultRunCoordinator().execute_runtime(
            RuntimeExecutionStub(request)
        )


@pytest.mark.asyncio
async def test_default_run_coordinator_rejects_managed_runtime_execution() -> None:
    target = RunTarget(
        workspace=CloudWorkspaceRef(
            workspace_url="docker://workspace/ws-1",
            workspace_id="ws-1",
        ),
        executor=ManagedPoolExecutorRef(pool="cloud"),
        isolation=IsolationPolicy(kind="provider_sandbox"),
    )
    request = RunRequest(session_id="session-1", run_id="run-1", target=target)

    with pytest.raises(
        RunCoordinatorError,
        match="managed_pool runtime execution is not available through this coordinator",
    ):
        _ = await DefaultRunCoordinator(
            local_daemon_executor=RecordingRunExecutor(),
        ).execute_runtime(RuntimeExecutionStub(request))


def test_run_request_rejects_empty_ids_and_metadata() -> None:
    with pytest.raises(ValueError, match="session_id must be non-empty"):
        _ = RunRequest(session_id=" ", run_id="run-1", target=_local_target())

    with pytest.raises(ValueError, match="run_id must be non-empty"):
        _ = RunRequest(session_id="session-1", run_id="", target=_local_target())

    with pytest.raises(ValueError, match="metadata keys must be non-empty"):
        _ = RunRequest(
            session_id="session-1",
            run_id="run-1",
            target=_local_target(),
            metadata={" ": "invalid"},
        )

    with pytest.raises(ValueError, match="metadata value for source must be non-empty"):
        _ = RunRequest(
            session_id="session-1",
            run_id="run-1",
            target=_local_target(),
            metadata={"source": " "},
        )


def test_run_request_rejects_empty_optional_refs() -> None:
    with pytest.raises(ValueError, match="input_summary must be non-empty"):
        _ = RunRequest(
            session_id="session-1",
            run_id="run-1",
            target=_local_target(),
            input_summary=" ",
        )

    with pytest.raises(ValueError, match="input_ref must be non-empty"):
        _ = RunRequest(
            session_id="session-1",
            run_id="run-1",
            target=_local_target(),
            input_ref=" ",
        )

    with pytest.raises(ValueError, match="resume_from_run_id must be non-empty"):
        _ = RunRequest(
            session_id="session-1",
            run_id="run-1",
            target=_local_target(),
            resume_from_run_id=" ",
        )


def test_run_submission_rejects_empty_ids() -> None:
    target = _local_target()

    with pytest.raises(ValueError, match="session_id must be non-empty"):
        _ = RunSubmission(
            session_id="",
            run_id="run-1",
            target=target,
            executor=target.executor,
        )

    with pytest.raises(ValueError, match="run_id must be non-empty"):
        _ = RunSubmission(
            session_id="session-1",
            run_id=" ",
            target=target,
            executor=target.executor,
        )


def test_run_request_copies_metadata() -> None:
    metadata = {"source": "cli"}

    request = RunRequest(
        session_id="session-1",
        run_id="run-1",
        target=_local_target(),
        metadata=metadata,
    )
    metadata["source"] = "changed"

    assert request.metadata == {"source": "cli"}
