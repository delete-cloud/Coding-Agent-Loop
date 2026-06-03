from __future__ import annotations

from dataclasses import dataclass

import pytest

from coding_agent.runs import (
    IsolationPolicy,
    LocalDaemonExecutorRef,
    LocalPathWorkspaceRef,
    RuntimePreparationRequestService,
    RunTarget,
)


def _target() -> RunTarget:
    return RunTarget(
        workspace=LocalPathWorkspaceRef(path="/repo"),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="default_local_sandbox"),
    )


@dataclass
class FakeSession:
    id: str = "session-1"
    default_run_target: RunTarget | None = None


def test_runtime_preparation_request_service_builds_default_request() -> None:
    target = _target()
    service = RuntimePreparationRequestService(run_id_factory=lambda: "fixed-id")

    request = service.request_for_session(FakeSession(default_run_target=target))

    assert request.session_id == "session-1"
    assert request.run_id == "runtime-prepare-fixed-id"
    assert request.target is target
    assert request.metadata == {"purpose": "runtime_preparation"}


def test_runtime_preparation_request_service_builds_purpose_request() -> None:
    target = _target()
    service = RuntimePreparationRequestService(run_id_factory=lambda: "restore-id")

    request = service.request_for_session(
        FakeSession(default_run_target=target),
        purpose="checkpoint_restore",
    )

    assert request.run_id == "runtime-prepare-restore-id"
    assert request.target is target
    assert request.metadata == {"purpose": "checkpoint_restore"}


def test_runtime_preparation_request_service_rejects_missing_target() -> None:
    service = RuntimePreparationRequestService(run_id_factory=lambda: "unused")

    with pytest.raises(RuntimeError, match="missing default_run_target"):
        service.request_for_session(FakeSession(default_run_target=None))
