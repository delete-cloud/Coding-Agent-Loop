from __future__ import annotations

import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agentkit.tape.tape import Tape
from coding_agent.approval import ApprovalPolicy
from coding_agent.executors import (
    LocalDaemonExecutor,
    LocalDaemonRuntimeBinding,
    LocalDaemonRuntimePreparation,
)
from coding_agent.runs import (
    CheckpointSessionConfig,
    InlineExecutorRef,
    IsolationPolicy,
    LocalDaemonExecutorRef,
    LocalPathWorkspaceRef,
    RunRequest,
    RunTarget,
)
from coding_agent.runs.checkpoint_runtime import CheckpointRuntimeBuilder


@dataclass
class FakeSession:
    id: str = "session-1"
    provider_name: str | None = "provider-a"
    model_name: str | None = "model-a"
    base_url: str | None = "http://provider.local"
    provider: Any | None = None
    default_run_target: RunTarget | None = None
    wire: Any = "wire"


class FakeAdapter:
    def __init__(self, pipeline: object, ctx: object, consumer: object) -> None:
        del pipeline, consumer
        self.ctx = ctx
        self.plugin_states_at_initialize: dict[str, object] | None = None

    async def initialize(self) -> None:
        self.plugin_states_at_initialize = dict(self.ctx.plugin_states)


def _local_target(path: str = "/repo") -> RunTarget:
    return RunTarget(
        workspace=LocalPathWorkspaceRef(path=path),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="default_local_sandbox"),
    )


def _config() -> CheckpointSessionConfig:
    return CheckpointSessionConfig(
        provider_name="provider-a",
        model_name="model-a",
        base_url="http://provider.local",
        max_steps=7,
        approval_policy=ApprovalPolicy.INTERACTIVE,
    )


def _builder(
    *,
    local_daemon_executor: LocalDaemonExecutor | None = None,
    observed_targets: list[RunTarget | None] | None = None,
    created_kwargs: dict[str, object] | None = None,
    semantic_topic_store: object | None = None,
) -> CheckpointRuntimeBuilder:
    def resolve_environment(target: RunTarget | None) -> object:
        if observed_targets is not None:
            observed_targets.append(target)
        return types.SimpleNamespace(target=target)

    def create_agent(**kwargs: object) -> tuple[object, object]:
        if created_kwargs is not None:
            created_kwargs.update(kwargs)
        llm_plugin = types.SimpleNamespace(_instance=None)
        pipeline = types.SimpleNamespace(
            _registry=types.SimpleNamespace(get=lambda name: llm_plugin)
        )
        ctx = types.SimpleNamespace(
            config={},
            plugin_states={},
            tape=kwargs["tape"],
            bound_subagent_publisher=False,
        )
        return pipeline, ctx

    return CheckpointRuntimeBuilder(
        local_daemon_executor=(
            LocalDaemonExecutor()
            if local_daemon_executor is None
            else local_daemon_executor
        ),
        resolve_environment_for_run_target=resolve_environment,
        workspace_root_for_environment=lambda environment: Path("/repo"),
        create_agent_for_session=create_agent,
        bind_subagent_message_publisher=lambda ctx: setattr(
            ctx,
            "bound_subagent_publisher",
            True,
        ),
        restore_consumer_factory=lambda wire: f"consumer:{wire}",
        adapter_factory=lambda pipeline, ctx, consumer: FakeAdapter(
            pipeline,
            ctx,
            consumer,
        ),
        semantic_topic_store_factory=lambda: semantic_topic_store,
        runtime_preparation_request=lambda session, *, purpose: RunRequest(
            session_id=session.id,
            run_id="runtime-prepare",
            target=session.default_run_target,
            metadata={"purpose": purpose},
        ),
    )


@pytest.mark.asyncio
async def test_checkpoint_runtime_builder_direct_injects_plugin_states_before_initialize() -> (
    None
):
    session = FakeSession(
        default_run_target=RunTarget(
            workspace=LocalPathWorkspaceRef(path="/repo"),
            executor=InlineExecutorRef(),
            isolation=IsolationPolicy(kind="default_local_sandbox"),
        )
    )
    session.provider = types.SimpleNamespace(model_name="model-a")
    created_kwargs: dict[str, object] = {}

    runtime = await _builder(created_kwargs=created_kwargs).prepare_runtime(
        session=session,
        restored_tape=Tape(entries=[], tape_id="tape-1"),
        restored_config=_config(),
        plugin_states={"topic": {"current_topic_id": "topic-1"}},
    )

    assert created_kwargs["model_override"] == "model-a"
    assert created_kwargs["provider_override"] == "provider-a"
    assert created_kwargs["base_url_override"] == "http://provider.local"
    assert created_kwargs["max_steps_override"] == 7
    assert created_kwargs["approval_mode_override"] == "interactive"
    assert runtime.ctx.config["wire_consumer"] == "consumer:wire"
    assert runtime.ctx.bound_subagent_publisher is True
    assert runtime.pipeline._registry.get("llm_provider")._instance is session.provider
    assert runtime.ctx.plugin_states == {"topic": {"current_topic_id": "topic-1"}}
    assert runtime.adapter.plugin_states_at_initialize == {
        "topic": {"current_topic_id": "topic-1"}
    }


@pytest.mark.asyncio
async def test_checkpoint_runtime_builder_threads_semantic_topic_store_to_create_agent() -> (
    None
):
    session = FakeSession(
        default_run_target=RunTarget(
            workspace=LocalPathWorkspaceRef(path="/repo"),
            executor=InlineExecutorRef(),
            isolation=IsolationPolicy(kind="default_local_sandbox"),
        )
    )
    created_kwargs: dict[str, object] = {}
    semantic_topic_store = object()

    await _builder(
        created_kwargs=created_kwargs,
        semantic_topic_store=semantic_topic_store,
    ).prepare_runtime(
        session=session,
        restored_tape=Tape(entries=[], tape_id="tape-1"),
        restored_config=_config(),
        plugin_states={},
    )

    assert created_kwargs["semantic_topic_store"] is semantic_topic_store


@pytest.mark.asyncio
async def test_checkpoint_runtime_builder_uses_local_daemon_preparation_for_local_targets() -> (
    None
):
    target = _local_target()
    observed_targets: list[RunTarget | None] = []

    class RecordingExecutor(LocalDaemonExecutor):
        def __init__(self) -> None:
            self.preparations: list[LocalDaemonRuntimePreparation] = []

        async def prepare_runtime(
            self,
            preparation: LocalDaemonRuntimePreparation,
        ) -> LocalDaemonRuntimeBinding:
            self._validate_request_target(preparation.request)
            self.preparations.append(preparation)
            return await preparation.runtime_provider.prepare_runtime(
                preparation.request
            )

    executor = RecordingExecutor()
    runtime = await _builder(
        local_daemon_executor=executor,
        observed_targets=observed_targets,
    ).prepare_runtime(
        session=FakeSession(default_run_target=target),
        restored_tape=Tape(entries=[], tape_id="tape-1"),
        restored_config=_config(),
        plugin_states={},
    )

    assert runtime.ctx.tape.tape_id == "tape-1"
    assert len(executor.preparations) == 1
    assert executor.preparations[0].request.target == target
    assert executor.preparations[0].request.metadata == {
        "purpose": "checkpoint_restore"
    }
    assert observed_targets == [target]
