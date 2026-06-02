from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from coding_agent.runs import (
    LocalDaemonExecutorRef,
    LocalPathWorkspaceRef,
    RunRequest,
    RunSubmission,
)


RuntimeTurnCallable = Callable[[], Awaitable[object]]


class RunExecutorTargetError(ValueError):
    """Raised when a run executor receives an incompatible target."""


@dataclass(frozen=True)
class LocalDaemonRuntimeExecution:
    request: RunRequest
    run: RuntimeTurnCallable


@dataclass(frozen=True)
class LocalDaemonExecutor:
    """Local daemon run executor boundary.

    This first slice validates and accepts local daemon run targets. Runtime
    ownership still moves in a later ADR-0058 slice.
    """

    async def submit_run(self, request: RunRequest) -> RunSubmission:
        self._validate_request_target(request)
        return RunSubmission(
            session_id=request.session_id,
            run_id=request.run_id,
            target=request.target,
            executor=request.target.executor,
            metadata=request.metadata,
        )

    async def execute_runtime(self, execution: LocalDaemonRuntimeExecution) -> object:
        self._validate_request_target(execution.request)
        return await execution.run()

    def _validate_request_target(self, request: RunRequest) -> None:
        if not isinstance(request.target.executor, LocalDaemonExecutorRef):
            raise RunExecutorTargetError(
                "LocalDaemonExecutor requires a local_daemon executor target"
            )
        if not isinstance(request.target.workspace, LocalPathWorkspaceRef):
            raise RunExecutorTargetError(
                "LocalDaemonExecutor requires a local_path workspace target"
            )
