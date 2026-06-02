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
    RunRequest,
    RunSubmission,
    RunTarget,
)


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


@pytest.mark.asyncio
async def test_default_run_coordinator_satisfies_protocol() -> None:
    target = _local_target()
    request = RunRequest(session_id="session-1", run_id="run-1", target=target)

    submission = await _submit_through_protocol(DefaultRunCoordinator(), request)

    assert submission.executor is target.executor
    assert submission.status == "accepted"


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
