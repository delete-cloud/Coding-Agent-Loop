from __future__ import annotations

import types
from pathlib import Path

import pytest

from coding_agent.approval import ApprovalPolicy
from coding_agent.environment import SandboxedEnvironment
from coding_agent.executors.local_daemon import LocalDaemonRuntimePreparation
from coding_agent.runs import (
    IsolationPolicy,
    LocalDaemonExecutorRef,
    LocalPathWorkspaceRef,
    ManagedPoolExecutorRef,
    RunRequest,
    RunTarget,
)
from coding_agent.runs.runtime_preparation import LocalDaemonRuntimePreparationService


class FakeBindingResolver:
    def resolve_environment(self, binding):
        raise AssertionError(f"unexpected binding resolution: {binding!r}")


class FakeRuntimeAdapter:
    def __init__(self) -> None:
        self.initialized = False

    async def initialize(self) -> None:
        self.initialized = True

    async def run_turn(self, prompt: str) -> object:
        return {"prompt": prompt}


class FakeRuntimeSession:
    def __init__(self) -> None:
        self.id = "session-1"
        self.model_name = "model-1"
        self.provider_name = "provider-1"
        self.base_url = "https://example.test"
        self.max_steps = 9
        self.approval_policy = ApprovalPolicy.INTERACTIVE
        self.provider = None
        self.tape_id: str | None = "tape-old"
        self.runtime_message_bus = object()
        self.default_run_target: RunTarget | None = None
        self.runtime_pipeline: object | None = None
        self.runtime_ctx: object | None = None
        self.runtime_adapter: FakeRuntimeAdapter | None = None


class FakeLocalDaemonExecutor:
    def __init__(self) -> None:
        self.preparations: list[LocalDaemonRuntimePreparation] = []

    async def prepare_runtime(self, preparation: LocalDaemonRuntimePreparation):
        self.preparations.append(preparation)
        return await preparation.runtime_provider.prepare_runtime(preparation.request)


def _request(workspace: Path) -> RunRequest:
    return RunRequest(
        session_id="session-1",
        run_id="run-1",
        target=RunTarget(
            workspace=LocalPathWorkspaceRef(path=str(workspace)),
            executor=LocalDaemonExecutorRef(),
            isolation=IsolationPolicy(kind="default_local_sandbox"),
        ),
        input_summary="implement",
    )


def _service(
    *,
    created: dict[str, object],
    persisted: list[str | None],
    restored: list[str | None],
    consumer: object,
    adapter: FakeRuntimeAdapter,
    executor: FakeLocalDaemonExecutor | None = None,
) -> LocalDaemonRuntimePreparationService:
    async def close_runtime(session: FakeRuntimeSession) -> None:
        session.runtime_pipeline = None
        session.runtime_ctx = None
        session.runtime_adapter = None

    async def restore_tape(tape_id: str | None) -> object:
        restored.append(tape_id)
        return types.SimpleNamespace(tape_id=tape_id)

    async def persist_session(session: FakeRuntimeSession) -> None:
        persisted.append(session.tape_id)

    async def close_runtime_adapter(runtime_adapter: object | None) -> None:
        del runtime_adapter

    def create_agent_for_session(**kwargs):
        created.update(kwargs)
        pipeline = types.SimpleNamespace(
            _registry=types.SimpleNamespace(
                get=lambda name: types.SimpleNamespace(name=name, _instance=None)
            )
        )
        ctx = types.SimpleNamespace(
            config={},
            tape=types.SimpleNamespace(tape_id="tape-new"),
            run_context=types.SimpleNamespace(environment=kwargs["environment"]),
        )
        return pipeline, ctx

    def runtime_preparation_request(session: FakeRuntimeSession) -> RunRequest:
        if session.default_run_target is None:
            raise RuntimeError("session is missing default_run_target")
        return RunRequest(
            session_id=session.id,
            run_id="runtime-prepare-1",
            target=session.default_run_target,
            metadata={"purpose": "runtime_preparation"},
        )

    return LocalDaemonRuntimePreparationService(
        binding_resolver=FakeBindingResolver(),
        local_daemon_executor=executor or FakeLocalDaemonExecutor(),
        close_runtime=close_runtime,
        close_runtime_adapter=close_runtime_adapter,
        create_agent_for_session=create_agent_for_session,
        restore_tape=restore_tape,
        persist_session=persist_session,
        make_consumer=lambda session: consumer,
        bind_subagent_message_publisher=lambda ctx: ctx.config.update(
            {"subagent_message_publisher": "bound"}
        ),
        runtime_preparation_request=runtime_preparation_request,
        adapter_factory=lambda pipeline, ctx, runtime_consumer: adapter,
    )


@pytest.mark.asyncio
async def test_runtime_preparation_service_builds_local_daemon_runtime(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    session = FakeRuntimeSession()
    created: dict[str, object] = {}
    persisted: list[str | None] = []
    restored: list[str | None] = []
    consumer = object()
    adapter = FakeRuntimeAdapter()

    binding = await _service(
        created=created,
        persisted=persisted,
        restored=restored,
        consumer=consumer,
        adapter=adapter,
    ).prepare_runtime(
        session,
        consumer=consumer,
        request=_request(workspace),
    )

    assert created["workspace_root"] == workspace.resolve()
    assert isinstance(created["environment"], SandboxedEnvironment)
    assert created["environment"].workspace_root == workspace.resolve()
    assert created["environment"].tool_config()["shell"] == {"sandbox_mode": "none"}
    assert created["environment"].tool_config()["isolation_policy"] == (
        _request(workspace).target.isolation.to_dict()
    )
    assert created["model_override"] == "model-1"
    assert created["provider_override"] == "provider-1"
    assert created["base_url_override"] == "https://example.test"
    assert created["max_steps_override"] == 9
    assert created["approval_mode_override"] == "interactive"
    assert created["session_id_override"] == "session-1"
    assert created["run_id_override"] == "run-1"
    assert created["api_key"] is None
    assert restored == ["tape-old"]
    assert persisted == ["tape-new"]
    assert session.tape_id == "tape-new"
    assert binding.adapter is adapter
    assert session.runtime_adapter is adapter


@pytest.mark.asyncio
async def test_runtime_preparation_service_builds_runtime_through_executor(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    session = FakeRuntimeSession()
    session.default_run_target = _request(workspace).target
    created: dict[str, object] = {}
    persisted: list[str | None] = []
    restored: list[str | None] = []
    executor = FakeLocalDaemonExecutor()
    adapter = FakeRuntimeAdapter()

    runtime = await _service(
        created=created,
        persisted=persisted,
        restored=restored,
        consumer=object(),
        adapter=adapter,
        executor=executor,
    ).build_runtime(session)

    assert len(executor.preparations) == 1
    assert executor.preparations[0].request.target is session.default_run_target
    assert executor.preparations[0].request.metadata == {
        "purpose": "runtime_preparation"
    }
    assert created["workspace_root"] == workspace.resolve()
    assert isinstance(created["environment"], SandboxedEnvironment)
    assert created["approval_mode_override"] == "interactive"
    assert restored == ["tape-old"]
    assert runtime.adapter is adapter
    assert adapter.initialized is True


@pytest.mark.asyncio
async def test_runtime_preparation_service_rejects_non_local_daemon_target(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    session = FakeRuntimeSession()
    request = RunRequest(
        session_id="session-1",
        run_id="run-1",
        target=RunTarget(
            workspace=LocalPathWorkspaceRef(path=str(workspace)),
            executor=ManagedPoolExecutorRef(pool="default"),
            isolation=IsolationPolicy(kind="provider_default"),
        ),
    )

    with pytest.raises(
        ValueError,
        match="local daemon runs require a local_daemon executor target",
    ):
        await _service(
            created={},
            persisted=[],
            restored=[],
            consumer=object(),
            adapter=FakeRuntimeAdapter(),
        ).prepare_runtime(
            session,
            consumer=object(),
            request=request,
        )
