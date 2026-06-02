from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from inspect import isawaitable
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
class LocalDaemonRuntimeBinding:
    pipeline: object
    ctx: object
    adapter: RuntimeTurnAdapter


class LocalDaemonRuntimeProvider(Protocol):
    async def prepare_runtime(self, request: RunRequest) -> LocalDaemonRuntimeBinding:
        ...


RuntimePreparedHook = Callable[
    [LocalDaemonRuntimeBinding],
    Awaitable[None] | None,
]
RuntimeTurnCompletedHook = Callable[
    [LocalDaemonRuntimeBinding, object],
    Awaitable[None] | None,
]
RuntimeTurnFailedHook = Callable[
    [LocalDaemonRuntimeBinding, BaseException],
    Awaitable[None] | None,
]


@dataclass(frozen=True)
class LocalDaemonRuntimeExecution:
    request: RunRequest
    runtime_provider: LocalDaemonRuntimeProvider
    prompt: str
    before_turn: RuntimePreparedHook | None = None
    after_turn: RuntimeTurnCompletedHook | None = None
    on_turn_error: RuntimeTurnFailedHook | None = None


@dataclass(frozen=True)
class LocalDaemonRuntimeResult:
    binding: LocalDaemonRuntimeBinding
    outcome: object


@dataclass(frozen=True)
class LocalDaemonExecutor:
    """Local daemon run executor boundary.

    This boundary owns local runtime preparation and adapter turn invocation.
    Session-specific persistence is still supplied by the caller while that
    state moves out of SessionManager in later ADR-0058 slices.
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

    async def execute_runtime(
        self,
        execution: LocalDaemonRuntimeExecution,
    ) -> LocalDaemonRuntimeResult:
        self._validate_request_target(execution.request)
        binding = await execution.runtime_provider.prepare_runtime(execution.request)
        if execution.before_turn is not None:
            before_turn_result = execution.before_turn(binding)
            if isawaitable(before_turn_result):
                await before_turn_result
        try:
            outcome = await binding.adapter.run_turn(execution.prompt)
        except BaseException as exc:
            if execution.on_turn_error is not None:
                turn_error_result = execution.on_turn_error(binding, exc)
                if isawaitable(turn_error_result):
                    await turn_error_result
            raise
        if execution.after_turn is not None:
            after_turn_result = execution.after_turn(binding, outcome)
            if isawaitable(after_turn_result):
                await after_turn_result
        return LocalDaemonRuntimeResult(binding=binding, outcome=outcome)

    def _validate_request_target(self, request: RunRequest) -> None:
        if not isinstance(request.target.executor, LocalDaemonExecutorRef):
            raise RunExecutorTargetError(
                "LocalDaemonExecutor requires a local_daemon executor target"
            )
        if not isinstance(request.target.workspace, LocalPathWorkspaceRef):
            raise RunExecutorTargetError(
                "LocalDaemonExecutor requires a local_path workspace target"
            )
