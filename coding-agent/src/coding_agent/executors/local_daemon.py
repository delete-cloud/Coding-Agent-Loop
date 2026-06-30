from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from inspect import isawaitable
from pathlib import Path
from typing import Protocol, cast

from coding_agent.approval import ApprovalPolicy
from coding_agent.runs import (
    LocalDaemonExecutorRef,
    LocalPathWorkspaceRef,
    RunRequest,
    RunTarget,
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
    async def prepare_runtime(
        self, request: RunRequest
    ) -> LocalDaemonRuntimeBinding: ...


class LocalDaemonRuntimeSession(Protocol):
    id: str
    model_name: str | None
    provider_name: str | None
    base_url: str | None
    max_steps: int | None
    approval_policy: ApprovalPolicy
    provider: object | None
    additional_directories: list[str]
    tape_id: str | None
    runtime_message_bus: object
    runtime_pipeline: object | None
    runtime_ctx: object | None
    runtime_adapter: RuntimeTurnAdapter | None

    def attach_runtime_binding(
        self,
        *,
        pipeline: object,
        ctx: object,
        adapter: object,
    ) -> None: ...


RuntimeEnvironmentResolver = Callable[[RunTarget], object]
RuntimeWorkspaceRootResolver = Callable[[object], Path | None]
RuntimeContextWorkspaceRootResolver = Callable[[object], Path | None]
RuntimeCloseHook = Callable[[LocalDaemonRuntimeSession], Awaitable[None]]
RuntimePersistHook = Callable[[LocalDaemonRuntimeSession], Awaitable[None]]
RuntimeTapeLoader = Callable[[str | None], Awaitable[object]]
RuntimeFactory = Callable[..., tuple[object, object]]
RuntimeAdapterFactory = Callable[[object, object], RuntimeTurnAdapter]
SemanticTopicStoreFactory = Callable[[], object | None]


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
class LocalDaemonRuntimePreparation:
    request: RunRequest
    runtime_provider: LocalDaemonRuntimeProvider


@dataclass(frozen=True)
class LocalDaemonRuntimeResult:
    binding: LocalDaemonRuntimeBinding
    outcome: object


@dataclass(frozen=True)
class LocalDaemonSessionRuntimeProvider:
    session: LocalDaemonRuntimeSession
    resolve_environment: RuntimeEnvironmentResolver
    workspace_root_for_environment: RuntimeWorkspaceRootResolver
    workspace_root_for_runtime: RuntimeContextWorkspaceRootResolver
    close_runtime: RuntimeCloseHook
    create_agent_for_session: RuntimeFactory
    restore_tape: RuntimeTapeLoader
    persist_session: RuntimePersistHook
    adapter_factory: RuntimeAdapterFactory
    semantic_topic_store_factory: SemanticTopicStoreFactory = lambda: None

    async def prepare_runtime(self, request: RunRequest) -> LocalDaemonRuntimeBinding:
        session = self.session
        pipeline = session.runtime_pipeline
        ctx = session.runtime_ctx
        adapter = session.runtime_adapter
        environment = self.resolve_environment(request.target)
        workspace_root = self.workspace_root_for_environment(environment)
        if pipeline is not None and ctx is not None and adapter is not None:
            cached_workspace_root = self.workspace_root_for_runtime(ctx)
            if (
                cached_workspace_root is not None
                and workspace_root is not None
                and cached_workspace_root != workspace_root
            ):
                await self.close_runtime(session)
                pipeline = None
                ctx = None
                adapter = None
        if pipeline is None or ctx is None or adapter is None:
            approval_mode_map = {
                ApprovalPolicy.YOLO: "yolo",
                ApprovalPolicy.INTERACTIVE: "interactive",
                ApprovalPolicy.AUTO: "auto",
            }
            pipeline, ctx = self.create_agent_for_session(
                workspace_root=workspace_root,
                environment=environment,
                model_override=session.model_name,
                provider_override=session.provider_name,
                base_url_override=session.base_url,
                max_steps_override=session.max_steps,
                approval_mode_override=approval_mode_map[session.approval_policy],
                session_id_override=session.id,
                run_id_override=request.run_id,
                api_key=None,
                mcp_servers_override=dict(getattr(session, "mcp_servers", {})),
                additional_workspace_roots_override=list(
                    getattr(session, "additional_directories", [])
                ),
                semantic_topic_store=self.semantic_topic_store_factory(),
                tape=await self.restore_tape(session.tape_id),
            )
            session.tape_id = ctx.tape.tape_id
            await self.persist_session(session)
            ctx.runtime_message_bus = session.runtime_message_bus
            ctx.config["wire_consumer"] = None
            ctx.config["agent_id"] = ""

            llm_plugin = pipeline._registry.get("llm_provider")
            if session.provider is not None:
                llm_plugin._instance = session.provider

            adapter = self.adapter_factory(pipeline, ctx)
            session.attach_runtime_binding(
                pipeline=pipeline,
                ctx=ctx,
                adapter=adapter,
            )
        return LocalDaemonRuntimeBinding(
            pipeline=pipeline,
            ctx=ctx,
            adapter=cast(RuntimeTurnAdapter, adapter),
        )


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

    async def prepare_runtime(
        self,
        preparation: LocalDaemonRuntimePreparation,
    ) -> LocalDaemonRuntimeBinding:
        self._validate_request_target(preparation.request)
        return await preparation.runtime_provider.prepare_runtime(preparation.request)

    async def execute_runtime(
        self,
        execution: LocalDaemonRuntimeExecution,
    ) -> LocalDaemonRuntimeResult:
        self._validate_request_target(execution.request)
        binding = await self.prepare_runtime(
            LocalDaemonRuntimePreparation(
                request=execution.request,
                runtime_provider=execution.runtime_provider,
            )
        )
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
