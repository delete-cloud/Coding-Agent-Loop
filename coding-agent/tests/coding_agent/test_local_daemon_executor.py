from __future__ import annotations

import pytest

from coding_agent.executors import LocalDaemonExecutor, RunExecutorTargetError
from coding_agent.runs import (
    CloudWorkspaceRef,
    IsolationPolicy,
    LocalDaemonExecutorRef,
    LocalPathWorkspaceRef,
    ManagedPoolExecutorRef,
    RunRequest,
    RunTarget,
)


def _local_request() -> RunRequest:
    return RunRequest(
        session_id="session-1",
        run_id="run-1",
        target=RunTarget(
            workspace=LocalPathWorkspaceRef(path="/repo"),
            executor=LocalDaemonExecutorRef(),
            isolation=IsolationPolicy(kind="default_local_sandbox"),
        ),
        input_summary="implement the task",
    )


@pytest.mark.asyncio
async def test_local_daemon_executor_accepts_local_daemon_target() -> None:
    request = _local_request()

    submission = await LocalDaemonExecutor().submit_run(request)

    assert submission.session_id == "session-1"
    assert submission.run_id == "run-1"
    assert submission.target is request.target
    assert isinstance(submission.executor, LocalDaemonExecutorRef)
    assert submission.status == "accepted"


@pytest.mark.asyncio
async def test_local_daemon_executor_rejects_non_local_daemon_executor() -> None:
    request = RunRequest(
        session_id="session-1",
        run_id="run-1",
        target=RunTarget(
            workspace=CloudWorkspaceRef(
                workspace_url="docker://workspace/ws-1",
                workspace_id="ws-1",
            ),
            executor=ManagedPoolExecutorRef(),
            isolation=IsolationPolicy(
                kind="provider_sandbox",
                network="provider_managed",
                filesystem="provider_managed",
                secrets="provider_managed",
            ),
        ),
    )

    with pytest.raises(
        RunExecutorTargetError,
        match="LocalDaemonExecutor requires a local_daemon executor target",
    ):
        _ = await LocalDaemonExecutor().submit_run(request)


@pytest.mark.asyncio
async def test_local_daemon_executor_rejects_non_local_workspace() -> None:
    request = RunRequest(
        session_id="session-1",
        run_id="run-1",
        target=RunTarget(
            workspace=CloudWorkspaceRef(
                workspace_url="docker://workspace/ws-1",
                workspace_id="ws-1",
            ),
            executor=LocalDaemonExecutorRef(),
            isolation=IsolationPolicy(kind="default_local_sandbox"),
        ),
    )

    with pytest.raises(
        RunExecutorTargetError,
        match="LocalDaemonExecutor requires a local_path workspace target",
    ):
        _ = await LocalDaemonExecutor().submit_run(request)
