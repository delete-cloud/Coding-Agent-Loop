from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from coding_agent.runs import (
    LocalDaemonExecutorRef,
    LocalPathWorkspaceRef,
    RunRequest,
    RunSubmission,
)


class RunExecutorTargetError(ValueError):
    """Raised when a run executor receives an incompatible target."""


class RuntimeTurnAdapter(Protocol):
    async def run_turn(self, prompt: str) -> object: ...


@dataclass(frozen=True)
class LocalDaemonRuntimeExecution:
    request: RunRequest
    adapter: RuntimeTurnAdapter
    prompt: str


@dataclass(frozen=True)
class LocalDaemonExecutor:
    """Local daemon run executor boundary.

    This slice owns the local adapter turn invocation. Full pipeline/context
    preparation still moves in later ADR-0058 slices.
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
        return await execution.adapter.run_turn(execution.prompt)

    def _validate_request_target(self, request: RunRequest) -> None:
        if not isinstance(request.target.executor, LocalDaemonExecutorRef):
            raise RunExecutorTargetError(
                "LocalDaemonExecutor requires a local_daemon executor target"
            )
        if not isinstance(request.target.workspace, LocalPathWorkspaceRef):
            raise RunExecutorTargetError(
                "LocalDaemonExecutor requires a local_path workspace target"
            )
