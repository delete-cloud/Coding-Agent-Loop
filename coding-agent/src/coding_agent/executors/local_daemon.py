from __future__ import annotations

from dataclasses import dataclass

from coding_agent.runs import (
    LocalDaemonExecutorRef,
    LocalPathWorkspaceRef,
    RunRequest,
    RunSubmission,
)


class RunExecutorTargetError(ValueError):
    """Raised when a run executor receives an incompatible target."""


@dataclass(frozen=True)
class LocalDaemonExecutor:
    """Local daemon run executor boundary.

    This first slice validates and accepts local daemon run targets. Runtime
    ownership still moves in a later ADR-0058 slice.
    """

    async def submit_run(self, request: RunRequest) -> RunSubmission:
        if not isinstance(request.target.executor, LocalDaemonExecutorRef):
            raise RunExecutorTargetError(
                "LocalDaemonExecutor requires a local_daemon executor target"
            )
        if not isinstance(request.target.workspace, LocalPathWorkspaceRef):
            raise RunExecutorTargetError(
                "LocalDaemonExecutor requires a local_path workspace target"
            )
        return RunSubmission(
            session_id=request.session_id,
            run_id=request.run_id,
            target=request.target,
            executor=request.target.executor,
            metadata=request.metadata,
        )
