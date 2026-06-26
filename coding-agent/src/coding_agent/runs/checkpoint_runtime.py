from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from inspect import isawaitable
from pathlib import Path
from typing import Any, Protocol

from agentkit.tape.tape import Tape

from coding_agent.approval import ApprovalPolicy
from coding_agent.executors import (
    LocalDaemonExecutor,
    LocalDaemonRuntimeBinding,
    LocalDaemonRuntimePreparation,
)
from coding_agent.runs.checkpoint_restore import (
    CheckpointRestoredRuntime,
    CheckpointSessionConfig,
)
from coding_agent.runs.coordinator import RunRequest
from coding_agent.runs.target import LocalDaemonExecutorRef, RunTarget


class CheckpointRuntimeSession(Protocol):
    id: str
    provider_name: str | None
    model_name: str | None
    base_url: str | None
    provider: Any | None
    default_run_target: RunTarget | None
    wire: Any


RuntimeEnvironmentResolver = Callable[[RunTarget | None], object]
RuntimeWorkspaceRootResolver = Callable[[object], Path | None]
CheckpointRuntimeFactory = Callable[..., tuple[object, object]]
SubagentMessagePublisherBinder = Callable[[object], None]
RestoreConsumerFactory = Callable[[object], object]
CheckpointRuntimeAdapterFactory = Callable[[object, object, object], object]
RuntimePreparationRequestFactory = Callable[..., RunRequest]
SemanticTopicStoreFactory = Callable[[], object | None]


@dataclass(frozen=True)
class _CheckpointLocalDaemonRuntimeProvider:
    prepare: Callable[[RunRequest], Awaitable[LocalDaemonRuntimeBinding]]

    async def prepare_runtime(self, request: RunRequest) -> LocalDaemonRuntimeBinding:
        return await self.prepare(request)


@dataclass(frozen=True)
class CheckpointRuntimeBuilder:
    local_daemon_executor: LocalDaemonExecutor
    resolve_environment_for_run_target: RuntimeEnvironmentResolver
    workspace_root_for_environment: RuntimeWorkspaceRootResolver
    create_agent_for_session: CheckpointRuntimeFactory
    bind_subagent_message_publisher: SubagentMessagePublisherBinder
    restore_consumer_factory: RestoreConsumerFactory
    adapter_factory: CheckpointRuntimeAdapterFactory
    runtime_preparation_request: RuntimePreparationRequestFactory
    semantic_topic_store_factory: SemanticTopicStoreFactory = lambda: None

    async def prepare_runtime(
        self,
        *,
        session: CheckpointRuntimeSession,
        restored_tape: Tape,
        restored_config: CheckpointSessionConfig,
        plugin_states: Mapping[str, Any],
    ) -> CheckpointRestoredRuntime:
        if not self._is_local_daemon_run_target(session.default_run_target):
            return await self._prepare_direct(
                session,
                restored_tape=restored_tape,
                restored_config=restored_config,
                plugin_states=plugin_states,
            )

        async def prepare_local_runtime(
            request: RunRequest,
        ) -> LocalDaemonRuntimeBinding:
            runtime = await self._prepare_direct(
                session,
                target=request.target,
                restored_tape=restored_tape,
                restored_config=restored_config,
                plugin_states=plugin_states,
            )
            return LocalDaemonRuntimeBinding(
                pipeline=runtime.pipeline,
                ctx=runtime.ctx,
                adapter=runtime.adapter,
            )

        binding = await self.local_daemon_executor.prepare_runtime(
            LocalDaemonRuntimePreparation(
                request=self.runtime_preparation_request(
                    session,
                    purpose="checkpoint_restore",
                ),
                runtime_provider=_CheckpointLocalDaemonRuntimeProvider(
                    prepare=prepare_local_runtime,
                ),
            )
        )
        return CheckpointRestoredRuntime(
            pipeline=binding.pipeline,
            ctx=binding.ctx,
            adapter=binding.adapter,
        )

    async def _prepare_direct(
        self,
        session: CheckpointRuntimeSession,
        *,
        restored_tape: Tape,
        restored_config: CheckpointSessionConfig,
        plugin_states: Mapping[str, Any],
        target: RunTarget | None = None,
    ) -> CheckpointRestoredRuntime:
        runtime_target = session.default_run_target if target is None else target
        environment = self.resolve_environment_for_run_target(runtime_target)
        workspace_root = self.workspace_root_for_environment(environment)
        pipeline, ctx = self.create_agent_for_session(
            workspace_root=workspace_root,
            environment=environment,
            model_override=restored_config.model_name,
            provider_override=restored_config.provider_name,
            base_url_override=restored_config.base_url,
            max_steps_override=restored_config.max_steps,
            approval_mode_override=self._approval_mode(restored_config.approval_policy),
            session_id_override=session.id,
            api_key=None,
            semantic_topic_store=self.semantic_topic_store_factory(),
            tape=restored_tape,
        )
        ctx.config["wire_consumer"] = None
        ctx.config["agent_id"] = ""
        self.bind_subagent_message_publisher(ctx)

        if self._can_reuse_provider(session, restored_config):
            llm_plugin = pipeline._registry.get("llm_provider")
            llm_plugin._instance = session.provider

        consumer = self.restore_consumer_factory(session.wire)
        ctx.config["wire_consumer"] = consumer
        for key, value in plugin_states.items():
            ctx.plugin_states.setdefault(key, value)
        adapter = self.adapter_factory(pipeline, ctx, consumer)
        initialize = getattr(adapter, "initialize", None)
        if callable(initialize):
            initialize_result = initialize()
            if isawaitable(initialize_result):
                await initialize_result
        return CheckpointRestoredRuntime(
            pipeline=pipeline,
            ctx=ctx,
            adapter=adapter,
        )

    def _can_reuse_provider(
        self,
        session: CheckpointRuntimeSession,
        restored_config: CheckpointSessionConfig,
    ) -> bool:
        provider_model_name = getattr(session.provider, "model_name", None)
        return (
            session.provider is not None
            and session.provider_name == restored_config.provider_name
            and provider_model_name == restored_config.model_name
            and session.base_url == restored_config.base_url
        )

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
    "CheckpointRuntimeBuilder",
    "CheckpointRuntimeSession",
]
