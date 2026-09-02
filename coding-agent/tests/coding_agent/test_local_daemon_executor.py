from __future__ import annotations

import asyncio
import types
from pathlib import Path
from typing import cast

import pytest

from coding_agent.approval import ApprovalPolicy
from coding_agent.executors import (
    LocalDaemonExecutor,
    LocalDaemonRuntimeBinding,
    LocalDaemonRuntimeExecution,
    LocalDaemonRuntimePreparation,
    LocalDaemonSessionRuntimeProvider,
    RunExecutorTargetError,
)
from coding_agent.runtime_activation import RUNTIME_VERSION_NEW
from coding_agent.runs import (
    CloudWorkspaceRef,
    IsolationPolicy,
    LocalDaemonExecutorRef,
    LocalPathWorkspaceRef,
    ManagedPoolExecutorRef,
    RunRequest,
    RunTarget,
)


def _local_request() -> RunRequest:
    return _local_request_for_path("/repo")


def _local_request_for_path(path: str, *, run_id: str = "run-1") -> RunRequest:
    return RunRequest(
        session_id="session-1",
        run_id=run_id,
        target=RunTarget(
            workspace=LocalPathWorkspaceRef(path=path),
            executor=LocalDaemonExecutorRef(),
            isolation=IsolationPolicy(kind="default_local_sandbox"),
        ),
        input_summary="implement the task",
    )


class FakeRuntimeAdapter:
    def __init__(self, *, result: object = "completed") -> None:
        self.result: object = result
        self.prompts: list[str] = []

    async def run_turn(self, prompt: str) -> object:
        self.prompts.append(prompt)
        return self.result


class FakeRuntimeProvider:
    def __init__(self, binding: LocalDaemonRuntimeBinding) -> None:
        self.binding = binding
        self.requests: list[RunRequest] = []

    async def prepare_runtime(self, request: RunRequest) -> LocalDaemonRuntimeBinding:
        self.requests.append(request)
        return self.binding


class FailingRuntimeAdapter:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc
        self.prompts: list[str] = []

    async def run_turn(self, prompt: str) -> object:
        self.prompts.append(prompt)
        raise self.exc


class FakeRuntimeSession:
    def __init__(self) -> None:
        self.id = "session-1"
        self.model_name = "model-1"
        self.provider_name = "provider-1"
        self.base_url = "https://example.test"
        self.api_key: str | None = "sk-session-key"
        self.max_steps = 7
        self.approval_policy = ApprovalPolicy.AUTO
        self.provider = None
        self.tape_id: str | None = "tape-old"
        self.runtime_message_bus = object()
        self.runtime_pipeline: object | None = None
        self.runtime_ctx: object | None = None
        self.runtime_adapter: FakeRuntimeAdapter | None = None
        self.runtime_version = "legacy"

    def attach_runtime_binding(
        self,
        *,
        pipeline: object,
        ctx: object,
        adapter: object,
    ) -> None:
        self.runtime_pipeline = pipeline
        self.runtime_ctx = ctx
        self.runtime_adapter = cast(FakeRuntimeAdapter, adapter)


@pytest.mark.asyncio
async def test_local_daemon_session_runtime_provider_reuses_cached_runtime(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    session = FakeRuntimeSession()
    cached_pipeline = object()
    cached_ctx = types.SimpleNamespace(
        config={}, tape=types.SimpleNamespace(tape_id="tape-old")
    )
    cached_adapter = FakeRuntimeAdapter()
    session.runtime_pipeline = cached_pipeline
    session.runtime_ctx = cached_ctx
    session.runtime_adapter = cached_adapter

    async def close_runtime(runtime_session) -> None:
        del runtime_session
        raise AssertionError("cached runtime should be reused")

    async def restore_tape(tape_id: str | None) -> object:
        del tape_id
        raise AssertionError("cached runtime should not restore tape")

    async def persist_session(runtime_session) -> None:
        del runtime_session
        raise AssertionError("cached runtime should not persist")

    def create_agent_for_session(**kwargs):
        raise AssertionError(f"cached runtime should not create agent: {kwargs!r}")

    binding = await LocalDaemonSessionRuntimeProvider(
        session=session,
        resolve_environment=lambda target: target,
        workspace_root_for_environment=lambda environment: workspace.resolve(),
        workspace_root_for_runtime=lambda ctx: workspace.resolve(),
        close_runtime=close_runtime,
        create_agent_for_session=create_agent_for_session,
        restore_tape=restore_tape,
        persist_session=persist_session,
        adapter_factory=lambda pipeline, ctx: FakeRuntimeAdapter(),
    ).prepare_runtime(_local_request_for_path(str(workspace)))

    assert binding.pipeline is cached_pipeline
    assert binding.ctx is cached_ctx
    assert binding.adapter is cached_adapter


@pytest.mark.asyncio
async def test_new_runtime_rebuilds_adapter_for_each_run(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    session = FakeRuntimeSession()
    session.runtime_version = RUNTIME_VERSION_NEW
    cached_pipeline = object()
    cached_ctx = types.SimpleNamespace(
        config={}, tape=types.SimpleNamespace(tape_id="tape-old")
    )
    session.runtime_pipeline = cached_pipeline
    session.runtime_ctx = cached_ctx
    session.runtime_adapter = FakeRuntimeAdapter()
    created_for: list[str] = []

    async def build_new_adapter(runtime_session, request: RunRequest) -> object:
        assert runtime_session is session
        created_for.append(request.run_id)
        return FakeRuntimeAdapter(result=request.run_id)

    async def close_runtime(runtime_session) -> None:
        del runtime_session
        raise AssertionError("same-workspace runtime context must stay attached")

    async def restore_tape(tape_id: str | None) -> object:
        del tape_id
        raise AssertionError("cached context must not restore tape")

    async def persist_session(runtime_session) -> None:
        del runtime_session
        raise AssertionError("cached context must not persist")

    def create_agent_for_session(**kwargs):
        raise AssertionError(f"cached context must not create agent: {kwargs!r}")

    provider = LocalDaemonSessionRuntimeProvider(
        session=session,
        resolve_environment=lambda target: target,
        workspace_root_for_environment=lambda environment: workspace.resolve(),
        workspace_root_for_runtime=lambda ctx: workspace.resolve(),
        close_runtime=close_runtime,
        create_agent_for_session=create_agent_for_session,
        restore_tape=restore_tape,
        persist_session=persist_session,
        adapter_factory=lambda pipeline, ctx: FakeRuntimeAdapter(),
        new_runtime_adapter_factory=build_new_adapter,
    )

    first = await provider.prepare_runtime(
        _local_request_for_path(str(workspace), run_id="run-1")
    )
    second = await provider.prepare_runtime(
        _local_request_for_path(str(workspace), run_id="run-2")
    )

    assert first.pipeline is cached_pipeline
    assert first.ctx is cached_ctx
    assert second.pipeline is cached_pipeline
    assert second.ctx is cached_ctx
    assert first.adapter is not second.adapter
    assert first.adapter.result == "run-1"
    assert second.adapter.result == "run-2"
    assert created_for == ["run-1", "run-2"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_name", "expected_api_key"),
    [
        ("provider-1", "sk-session-key"),
        ("codex", None),
        ("codex:work", None),
    ],
)
async def test_local_daemon_session_runtime_provider_rebuilds_changed_workspace(
    tmp_path: Path,
    provider_name: str,
    expected_api_key: str | None,
) -> None:
    old_workspace = tmp_path / "old"
    old_workspace.mkdir()
    new_workspace = tmp_path / "new"
    new_workspace.mkdir()
    session = FakeRuntimeSession()
    session.provider_name = provider_name
    old_adapter = FakeRuntimeAdapter()
    session.runtime_pipeline = object()
    session.runtime_ctx = types.SimpleNamespace(
        config={}, tape=types.SimpleNamespace(tape_id="tape-old")
    )
    session.runtime_adapter = old_adapter
    closed: list[str] = []
    persisted: list[str | None] = []
    restored: list[str | None] = []
    create_kwargs: dict[str, object] = {}
    adapter = FakeRuntimeAdapter()
    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda name: types.SimpleNamespace(name=name, _instance=None)
        )
    )
    fake_ctx = types.SimpleNamespace(
        config={}, tape=types.SimpleNamespace(tape_id="tape-new")
    )

    async def close_runtime(runtime_session) -> None:
        closed.append(runtime_session.id)
        runtime_session.runtime_pipeline = None
        runtime_session.runtime_ctx = None
        runtime_session.runtime_adapter = None

    async def restore_tape(tape_id: str | None) -> object:
        restored.append(tape_id)
        return types.SimpleNamespace(tape_id=tape_id)

    async def persist_session(runtime_session) -> None:
        persisted.append(runtime_session.tape_id)

    def create_agent_for_session(**kwargs):
        create_kwargs.update(kwargs)
        return fake_pipeline, fake_ctx

    binding = await LocalDaemonSessionRuntimeProvider(
        session=session,
        resolve_environment=lambda target: target,
        workspace_root_for_environment=lambda environment: new_workspace.resolve(),
        workspace_root_for_runtime=lambda ctx: old_workspace.resolve(),
        close_runtime=close_runtime,
        create_agent_for_session=create_agent_for_session,
        restore_tape=restore_tape,
        persist_session=persist_session,
        adapter_factory=lambda pipeline, ctx: adapter,
    ).prepare_runtime(_local_request_for_path(str(new_workspace)))

    assert closed == ["session-1"]
    assert restored == ["tape-old"]
    assert persisted == ["tape-new"]
    assert create_kwargs["workspace_root"] == new_workspace.resolve()
    assert create_kwargs["model_override"] == "model-1"
    assert create_kwargs["provider_override"] == provider_name
    assert create_kwargs["base_url_override"] == "https://example.test"
    assert create_kwargs["max_steps_override"] == 7
    assert create_kwargs["approval_mode_override"] == "auto"
    assert create_kwargs["session_id_override"] == "session-1"
    assert create_kwargs["run_id_override"] == "run-1"
    assert create_kwargs["api_key"] == expected_api_key
    assert session.tape_id == "tape-new"
    assert fake_ctx.runtime_message_bus is session.runtime_message_bus
    assert fake_ctx.config == {"wire_consumer": None, "agent_id": ""}
    assert session.runtime_pipeline is fake_pipeline
    assert session.runtime_ctx is fake_ctx
    assert session.runtime_adapter is adapter
    assert binding.pipeline is fake_pipeline
    assert binding.ctx is fake_ctx
    assert binding.adapter is adapter


@pytest.mark.asyncio
async def test_local_daemon_session_runtime_provider_threads_semantic_topic_store(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    session = FakeRuntimeSession()
    create_kwargs: dict[str, object] = {}
    semantic_topic_store = object()
    adapter = FakeRuntimeAdapter()
    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda name: types.SimpleNamespace(name=name, _instance=None)
        )
    )
    fake_ctx = types.SimpleNamespace(
        config={}, tape=types.SimpleNamespace(tape_id="tape-new")
    )

    async def close_runtime(runtime_session) -> None:
        del runtime_session
        raise AssertionError("new runtime should not close cached runtime")

    async def restore_tape(tape_id: str | None) -> object:
        return types.SimpleNamespace(tape_id=tape_id)

    async def persist_session(runtime_session) -> None:
        del runtime_session

    def create_agent_for_session(**kwargs):
        create_kwargs.update(kwargs)
        return fake_pipeline, fake_ctx

    await LocalDaemonSessionRuntimeProvider(
        session=session,
        resolve_environment=lambda target: target,
        workspace_root_for_environment=lambda environment: workspace.resolve(),
        workspace_root_for_runtime=lambda ctx: workspace.resolve(),
        close_runtime=close_runtime,
        create_agent_for_session=create_agent_for_session,
        restore_tape=restore_tape,
        persist_session=persist_session,
        adapter_factory=lambda pipeline, ctx: adapter,
        semantic_topic_store_factory=lambda: semantic_topic_store,
    ).prepare_runtime(_local_request_for_path(str(workspace)))

    assert create_kwargs["semantic_topic_store"] is semantic_topic_store


@pytest.mark.asyncio
async def test_local_daemon_executor_accepts_local_daemon_target() -> None:
    request = _local_request()

    submission = await LocalDaemonExecutor().submit_run(request)

    assert submission.session_id == "session-1"
    assert submission.run_id == "run-1"
    assert submission.target is request.target
    assert isinstance(submission.executor, LocalDaemonExecutorRef)
    assert submission.status == "accepted"


@pytest.mark.asyncio
async def test_local_daemon_executor_runs_adapter_turn() -> None:
    request = _local_request()
    adapter = FakeRuntimeAdapter(result="completed")
    binding = LocalDaemonRuntimeBinding(
        pipeline=object(),
        ctx=object(),
        adapter=adapter,
    )
    provider = FakeRuntimeProvider(binding)

    result = await LocalDaemonExecutor().execute_runtime(
        LocalDaemonRuntimeExecution(
            request=request,
            runtime_provider=provider,
            prompt="implement the task",
        )
    )

    assert result.binding is binding
    assert result.outcome == "completed"
    assert provider.requests == [request]
    assert adapter.prompts == ["implement the task"]


@pytest.mark.asyncio
async def test_local_daemon_executor_prepares_runtime() -> None:
    request = _local_request()
    adapter = FakeRuntimeAdapter(result="prepared")
    binding = LocalDaemonRuntimeBinding(
        pipeline=object(),
        ctx=object(),
        adapter=adapter,
    )
    provider = FakeRuntimeProvider(binding)

    prepared = await LocalDaemonExecutor().prepare_runtime(
        LocalDaemonRuntimePreparation(
            request=request,
            runtime_provider=provider,
        )
    )

    assert prepared is binding
    assert provider.requests == [request]
    assert adapter.prompts == []


@pytest.mark.asyncio
async def test_local_daemon_executor_runs_before_turn_after_preparation() -> None:
    request = _local_request()
    adapter = FakeRuntimeAdapter(result="completed")
    binding = LocalDaemonRuntimeBinding(
        pipeline=object(),
        ctx=object(),
        adapter=adapter,
    )
    provider = FakeRuntimeProvider(binding)
    events: list[tuple[str, list[str]]] = []

    async def before_turn(prepared: LocalDaemonRuntimeBinding) -> None:
        assert prepared is binding
        events.append(("before_turn", list(adapter.prompts)))

    result = await LocalDaemonExecutor().execute_runtime(
        LocalDaemonRuntimeExecution(
            request=request,
            runtime_provider=provider,
            prompt="implement the task",
            before_turn=before_turn,
        )
    )

    assert result.outcome == "completed"
    assert events == [("before_turn", [])]
    assert adapter.prompts == ["implement the task"]


@pytest.mark.asyncio
async def test_local_daemon_executor_runs_after_turn_after_adapter() -> None:
    request = _local_request()
    adapter = FakeRuntimeAdapter(result={"status": "completed"})
    binding = LocalDaemonRuntimeBinding(
        pipeline=object(),
        ctx=object(),
        adapter=adapter,
    )
    provider = FakeRuntimeProvider(binding)
    events: list[tuple[str, list[str], object]] = []

    async def after_turn(
        prepared: LocalDaemonRuntimeBinding,
        outcome: object,
    ) -> None:
        assert prepared is binding
        events.append(("after_turn", list(adapter.prompts), outcome))

    result = await LocalDaemonExecutor().execute_runtime(
        LocalDaemonRuntimeExecution(
            request=request,
            runtime_provider=provider,
            prompt="implement the task",
            after_turn=after_turn,
        )
    )

    assert result.outcome == {"status": "completed"}
    assert events == [("after_turn", ["implement the task"], {"status": "completed"})]
    assert adapter.prompts == ["implement the task"]


@pytest.mark.asyncio
async def test_local_daemon_executor_runs_turn_error_hook_and_reraises() -> None:
    request = _local_request()
    error = RuntimeError("turn exploded")
    adapter = FailingRuntimeAdapter(error)
    binding = LocalDaemonRuntimeBinding(
        pipeline=object(),
        ctx=object(),
        adapter=adapter,
    )
    provider = FakeRuntimeProvider(binding)
    events: list[tuple[str, list[str], BaseException]] = []

    async def on_turn_error(
        prepared: LocalDaemonRuntimeBinding,
        exc: BaseException,
    ) -> None:
        assert prepared is binding
        events.append(("on_turn_error", list(adapter.prompts), exc))

    with pytest.raises(RuntimeError, match="turn exploded"):
        _ = await LocalDaemonExecutor().execute_runtime(
            LocalDaemonRuntimeExecution(
                request=request,
                runtime_provider=provider,
                prompt="implement the task",
                on_turn_error=on_turn_error,
            )
        )

    assert events == [("on_turn_error", ["implement the task"], error)]


@pytest.mark.asyncio
async def test_local_daemon_executor_runs_turn_error_hook_for_cancel() -> None:
    request = _local_request()
    error = asyncio.CancelledError()
    adapter = FailingRuntimeAdapter(error)
    binding = LocalDaemonRuntimeBinding(
        pipeline=object(),
        ctx=object(),
        adapter=adapter,
    )
    provider = FakeRuntimeProvider(binding)
    events: list[BaseException] = []

    async def on_turn_error(
        prepared: LocalDaemonRuntimeBinding,
        exc: BaseException,
    ) -> None:
        assert prepared is binding
        events.append(exc)

    with pytest.raises(asyncio.CancelledError):
        _ = await LocalDaemonExecutor().execute_runtime(
            LocalDaemonRuntimeExecution(
                request=request,
                runtime_provider=provider,
                prompt="cancel the task",
                on_turn_error=on_turn_error,
            )
        )

    assert events == [error]
    assert adapter.prompts == ["cancel the task"]


@pytest.mark.asyncio
async def test_local_daemon_executor_rejects_non_local_daemon_executor() -> None:
    request = RunRequest(
        session_id="session-1",
        run_id="run-1",
        target=RunTarget(
            workspace=CloudWorkspaceRef(
                workspace_url="docker://workspace/ws-1",
                workspace_id="ws-1",
            ),
            executor=ManagedPoolExecutorRef(),
            isolation=IsolationPolicy(
                kind="provider_sandbox",
                network="provider_managed",
                filesystem="provider_managed",
                secrets="provider_managed",
            ),
        ),
    )

    with pytest.raises(
        RunExecutorTargetError,
        match="LocalDaemonExecutor requires a local_daemon executor target",
    ):
        _ = await LocalDaemonExecutor().submit_run(request)


@pytest.mark.asyncio
async def test_local_daemon_executor_rejects_non_local_workspace() -> None:
    request = RunRequest(
        session_id="session-1",
        run_id="run-1",
        target=RunTarget(
            workspace=CloudWorkspaceRef(
                workspace_url="docker://workspace/ws-1",
                workspace_id="ws-1",
            ),
            executor=LocalDaemonExecutorRef(),
            isolation=IsolationPolicy(kind="default_local_sandbox"),
        ),
    )

    with pytest.raises(
        RunExecutorTargetError,
        match="LocalDaemonExecutor requires a local_path workspace target",
    ):
        _ = await LocalDaemonExecutor().submit_run(request)


@pytest.mark.asyncio
async def test_local_daemon_executor_rejects_runtime_for_non_local_target() -> None:
    request = RunRequest(
        session_id="session-1",
        run_id="run-1",
        target=RunTarget(
            workspace=CloudWorkspaceRef(
                workspace_url="docker://workspace/ws-1",
                workspace_id="ws-1",
            ),
            executor=ManagedPoolExecutorRef(),
            isolation=IsolationPolicy(
                kind="provider_sandbox",
                network="provider_managed",
                filesystem="provider_managed",
                secrets="provider_managed",
            ),
        ),
    )

    adapter = FakeRuntimeAdapter(result="should not execute")
    provider = FakeRuntimeProvider(
        LocalDaemonRuntimeBinding(
            pipeline=object(),
            ctx=object(),
            adapter=adapter,
        )
    )

    with pytest.raises(
        RunExecutorTargetError,
        match="LocalDaemonExecutor requires a local_daemon executor target",
    ):
        _ = await LocalDaemonExecutor().execute_runtime(
            LocalDaemonRuntimeExecution(
                request=request,
                runtime_provider=provider,
                prompt="should not run",
            )
        )

    assert provider.requests == []
    assert adapter.prompts == []


@pytest.mark.asyncio
async def test_local_daemon_executor_validates_runtime_before_overridden_prepare() -> (
    None
):
    request = RunRequest(
        session_id="session-1",
        run_id="run-1",
        target=RunTarget(
            workspace=CloudWorkspaceRef(
                workspace_url="docker://workspace/ws-1",
                workspace_id="ws-1",
            ),
            executor=ManagedPoolExecutorRef(),
            isolation=IsolationPolicy(
                kind="provider_sandbox",
                network="provider_managed",
                filesystem="provider_managed",
                secrets="provider_managed",
            ),
        ),
    )
    adapter = FakeRuntimeAdapter(result="should not execute")
    provider = FakeRuntimeProvider(
        LocalDaemonRuntimeBinding(
            pipeline=object(),
            ctx=object(),
            adapter=adapter,
        )
    )

    class UnsafePrepareExecutor(LocalDaemonExecutor):
        async def prepare_runtime(
            self,
            preparation: LocalDaemonRuntimePreparation,
        ) -> LocalDaemonRuntimeBinding:
            return await preparation.runtime_provider.prepare_runtime(
                preparation.request
            )

    with pytest.raises(
        RunExecutorTargetError,
        match="LocalDaemonExecutor requires a local_daemon executor target",
    ):
        _ = await UnsafePrepareExecutor().execute_runtime(
            LocalDaemonRuntimeExecution(
                request=request,
                runtime_provider=provider,
                prompt="should not run",
            )
        )

    assert provider.requests == []
    assert adapter.prompts == []


@pytest.mark.asyncio
async def test_local_daemon_executor_rejects_runtime_preparation_for_non_local_target() -> (
    None
):
    request = RunRequest(
        session_id="session-1",
        run_id="run-1",
        target=RunTarget(
            workspace=CloudWorkspaceRef(
                workspace_url="docker://workspace/ws-1",
                workspace_id="ws-1",
            ),
            executor=ManagedPoolExecutorRef(),
            isolation=IsolationPolicy(
                kind="provider_sandbox",
                network="provider_managed",
                filesystem="provider_managed",
                secrets="provider_managed",
            ),
        ),
    )

    adapter = FakeRuntimeAdapter(result="should not prepare")
    provider = FakeRuntimeProvider(
        LocalDaemonRuntimeBinding(
            pipeline=object(),
            ctx=object(),
            adapter=adapter,
        )
    )

    with pytest.raises(
        RunExecutorTargetError,
        match="LocalDaemonExecutor requires a local_daemon executor target",
    ):
        _ = await LocalDaemonExecutor().prepare_runtime(
            LocalDaemonRuntimePreparation(
                request=request,
                runtime_provider=provider,
            )
        )

    assert provider.requests == []
    assert adapter.prompts == []


@pytest.mark.asyncio
async def test_local_daemon_executor_rejects_runtime_preparation_for_non_local_workspace() -> (
    None
):
    request = RunRequest(
        session_id="session-1",
        run_id="run-1",
        target=RunTarget(
            workspace=CloudWorkspaceRef(
                workspace_url="docker://workspace/ws-1",
                workspace_id="ws-1",
            ),
            executor=LocalDaemonExecutorRef(),
            isolation=IsolationPolicy(
                kind="provider_sandbox",
                network="provider_managed",
                filesystem="provider_managed",
                secrets="provider_managed",
            ),
        ),
    )

    adapter = FakeRuntimeAdapter(result="should not prepare")
    provider = FakeRuntimeProvider(
        LocalDaemonRuntimeBinding(
            pipeline=object(),
            ctx=object(),
            adapter=adapter,
        )
    )

    with pytest.raises(
        RunExecutorTargetError,
        match="LocalDaemonExecutor requires a local_path workspace target",
    ):
        _ = await LocalDaemonExecutor().prepare_runtime(
            LocalDaemonRuntimePreparation(
                request=request,
                runtime_provider=provider,
            )
        )

    assert provider.requests == []
    assert adapter.prompts == []
