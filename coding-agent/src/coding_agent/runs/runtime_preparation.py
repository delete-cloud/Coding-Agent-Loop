from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from inspect import isawaitable
from pathlib import Path
from typing import Protocol, cast

from agentkit.environment import Environment

from coding_agent.adapter import PipelineAdapter
from coding_agent.approval import ApprovalPolicy
from coding_agent.environment.binding_resolver import BindingResolver
from coding_agent.environment.execution_binding import CloudWorkspaceBinding
from coding_agent.environment.local import LocalEnvironment
from coding_agent.environment.sandboxed import sandbox_environment
from coding_agent.executors.local_daemon import (
    LocalDaemonExecutor,
    LocalDaemonRuntimeBinding,
    LocalDaemonRuntimePreparation,
    LocalDaemonSessionRuntimeProvider,
    LocalDaemonRuntimeSession,
    RuntimeTurnAdapter,
)
from coding_agent.runs.coordinator import RunRequest
from coding_agent.runs.target import (
    CloudWorkspaceRef,
    LocalDaemonExecutorRef,
    LocalPathWorkspaceRef,
    RunTarget,
)


RuntimeCloseHook = Callable[[LocalDaemonRuntimeSession], Awaitable[None]]
RuntimeAdapterCloseHook = Callable[[object | None], Awaitable[None]]
RuntimePersistHook = Callable[[LocalDaemonRuntimeSession], Awaitable[None]]
RuntimeTapeLoader = Callable[[str | None], Awaitable[object]]
RuntimeFactory = Callable[..., tuple[object, object]]
RuntimeAdapterFactory = Callable[[object, object, object], RuntimeTurnAdapter]
SubagentMessagePublisherBinder = Callable[[object], None]
RuntimePreparationRequestFactory = Callable[..., RunRequest]


class RuntimeConsumer(Protocol):
    pass


class RuntimeConsumerFactory(Protocol):
    def __call__(self, session: LocalDaemonRuntimeSession) -> RuntimeConsumer: ...


@dataclass(frozen=True)
class RuntimeBuildResult:
    pipeline: object
    ctx: object
    adapter: RuntimeTurnAdapter


@dataclass(frozen=True)
class _SessionLocalDaemonRuntimeProvider:
    prepare: Callable[[RunRequest], Awaitable[LocalDaemonRuntimeBinding]]

    async def prepare_runtime(self, request: RunRequest) -> LocalDaemonRuntimeBinding:
        return await self.prepare(request)


@dataclass(frozen=True)
class LocalDaemonRuntimePreparationService:
    binding_resolver: BindingResolver
    local_daemon_executor: LocalDaemonExecutor
    close_runtime: RuntimeCloseHook
    close_runtime_adapter: RuntimeAdapterCloseHook
    create_agent_for_session: RuntimeFactory
    restore_tape: RuntimeTapeLoader
    persist_session: RuntimePersistHook
    make_consumer: RuntimeConsumerFactory
    bind_subagent_message_publisher: SubagentMessagePublisherBinder
    runtime_preparation_request: RuntimePreparationRequestFactory
    adapter_factory: RuntimeAdapterFactory = (
        lambda pipeline, ctx, consumer: PipelineAdapter(
            pipeline=pipeline,
            ctx=ctx,
            consumer=consumer,
        )
    )

    async def prepare_runtime(
        self,
        session: LocalDaemonRuntimeSession,
        *,
        consumer: RuntimeConsumer,
        request: RunRequest,
    ) -> LocalDaemonRuntimeBinding:
        return await LocalDaemonSessionRuntimeProvider(
            session=session,
            resolve_environment=self._resolve_local_daemon_environment,
            workspace_root_for_environment=self._environment_workspace_root,
            workspace_root_for_runtime=self._runtime_environment_workspace_root,
            close_runtime=self.close_runtime,
            create_agent_for_session=self.create_agent_for_session,
            restore_tape=self.restore_tape,
            persist_session=self.persist_session,
            adapter_factory=lambda pipeline, ctx: self.adapter_factory(
                pipeline,
                ctx,
                consumer,
            ),
        ).prepare_runtime(request)

    async def build_runtime(
        self,
        session: LocalDaemonRuntimeSession,
        *,
        model_name: str | None = None,
        provider_name: str | None = None,
        base_url: str | None = None,
        max_steps: int | None = None,
        approval_policy: ApprovalPolicy | None = None,
    ) -> RuntimeBuildResult:
        default_target = getattr(session, "default_run_target", None)
        if not self._is_local_daemon_run_target(default_target):
            return await self._build_runtime_direct(
                session,
                model_name=model_name,
                provider_name=provider_name,
                base_url=base_url,
                max_steps=max_steps,
                approval_policy=approval_policy,
            )

        async def prepare_runtime(request: RunRequest) -> LocalDaemonRuntimeBinding:
            runtime = await self._build_runtime_direct(
                session,
                target=request.target,
                model_name=model_name,
                provider_name=provider_name,
                base_url=base_url,
                max_steps=max_steps,
                approval_policy=approval_policy,
            )
            return LocalDaemonRuntimeBinding(
                pipeline=runtime.pipeline,
                ctx=runtime.ctx,
                adapter=runtime.adapter,
            )

        binding = await self.local_daemon_executor.prepare_runtime(
            LocalDaemonRuntimePreparation(
                request=self.runtime_preparation_request(session),
                runtime_provider=_SessionLocalDaemonRuntimeProvider(
                    prepare=prepare_runtime,
                ),
            )
        )
        return RuntimeBuildResult(
            pipeline=binding.pipeline,
            ctx=binding.ctx,
            adapter=cast(RuntimeTurnAdapter, binding.adapter),
        )

    async def _build_runtime_direct(
        self,
        session: LocalDaemonRuntimeSession,
        *,
        target: RunTarget | None = None,
        model_name: str | None = None,
        provider_name: str | None = None,
        base_url: str | None = None,
        max_steps: int | None = None,
        approval_policy: ApprovalPolicy | None = None,
    ) -> RuntimeBuildResult:
        default_target = getattr(session, "default_run_target", None)
        runtime_target = default_target if target is None else target
        if runtime_target is None:
            raise RuntimeError("session is missing default_run_target")

        resolved_provider_name = (
            session.provider_name if provider_name is None else provider_name
        )
        resolved_model_name = session.model_name if model_name is None else model_name
        resolved_base_url = session.base_url if base_url is None else base_url
        resolved_max_steps = session.max_steps if max_steps is None else max_steps
        resolved_approval_policy = (
            session.approval_policy if approval_policy is None else approval_policy
        )
        environment = self._resolve_environment_for_run_target(runtime_target)
        workspace_root = self._environment_workspace_root(environment)
        consumer = self.make_consumer(session)
        pipeline, ctx = self.create_agent_for_session(
            workspace_root=workspace_root,
            environment=environment,
            model_override=resolved_model_name,
            provider_override=resolved_provider_name,
            base_url_override=resolved_base_url,
            max_steps_override=resolved_max_steps,
            approval_mode_override=self._approval_mode(resolved_approval_policy),
            session_id_override=session.id,
            api_key=None,
            tape=await self.restore_tape(session.tape_id),
        )
        ctx.config["wire_consumer"] = consumer
        ctx.config["agent_id"] = ""
        self.bind_subagent_message_publisher(ctx)

        provider_model_name = getattr(session.provider, "model_name", None)
        if (
            session.provider is not None
            and session.provider_name == resolved_provider_name
            and provider_model_name == resolved_model_name
            and session.base_url == resolved_base_url
        ):
            llm_plugin = pipeline._registry.get("llm_provider")
            llm_plugin._instance = session.provider

        adapter = self.adapter_factory(pipeline, ctx, consumer)
        try:
            initialize = getattr(adapter, "initialize", None)
            if callable(initialize):
                initialize_result = initialize()
                if isawaitable(initialize_result):
                    await initialize_result
        except Exception:
            await self.close_runtime_adapter(adapter)
            raise
        return RuntimeBuildResult(pipeline=pipeline, ctx=ctx, adapter=adapter)

    def _resolve_local_daemon_environment(self, target: RunTarget) -> Environment:
        if not isinstance(target.executor, LocalDaemonExecutorRef):
            raise ValueError("local daemon runs require a local_daemon executor target")
        if not isinstance(target.workspace, LocalPathWorkspaceRef):
            raise ValueError("local daemon runs require a local_path workspace target")
        return self._resolve_environment_for_run_target(target)

    def _resolve_environment_for_run_target(self, target: RunTarget) -> Environment:
        workspace = target.workspace
        environment: Environment
        if isinstance(workspace, LocalPathWorkspaceRef):
            environment = LocalEnvironment(Path(workspace.path).expanduser().resolve())
            return sandbox_environment(environment, target.isolation)
        if isinstance(workspace, CloudWorkspaceRef):
            environment = self.binding_resolver.resolve_environment(
                CloudWorkspaceBinding(
                    workspace_url=workspace.workspace_url,
                    workspace_id=workspace.workspace_id,
                    runtime_profile=workspace.runtime_profile,
                    workspace_provider=workspace.workspace_provider,
                    provider_instance_id=workspace.provider_instance_id,
                )
            )
            return sandbox_environment(environment, target.isolation)
        raise ValueError(
            f"runtime builders cannot resolve workspace target: {workspace.kind}"
        )

    def _environment_workspace_root(self, environment: Environment) -> Path | None:
        local_root = environment.workspace_summary().local_root
        if local_root is None:
            return None
        return Path(local_root).expanduser().resolve()

    def _runtime_environment_workspace_root(self, ctx: object) -> Path | None:
        run_context = getattr(ctx, "run_context", None)
        environment = getattr(run_context, "environment", None)
        if environment is None:
            return None
        return self._environment_workspace_root(cast(Environment, environment))

    def _is_local_daemon_run_target(self, target: RunTarget | None) -> bool:
        if target is None:
            return False
        return isinstance(target.executor, LocalDaemonExecutorRef)

    def _approval_mode(self, approval_policy: ApprovalPolicy) -> str:
        approval_mode_map = {
            ApprovalPolicy.YOLO: "yolo",
            ApprovalPolicy.INTERACTIVE: "interactive",
            ApprovalPolicy.AUTO: "auto",
        }
        return approval_mode_map[approval_policy]


__all__ = [
    "LocalDaemonRuntimePreparationService",
    "RuntimeBuildResult",
    "RuntimeConsumer",
]
