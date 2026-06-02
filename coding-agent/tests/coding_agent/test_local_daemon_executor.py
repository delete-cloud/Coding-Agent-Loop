from __future__ import annotations

import pytest

from coding_agent.executors import (
    LocalDaemonExecutor,
    LocalDaemonRuntimeBinding,
    LocalDaemonRuntimeExecution,
    RunExecutorTargetError,
)
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
    return RunRequest(
        session_id="session-1",
        run_id="run-1",
        target=RunTarget(
            workspace=LocalPathWorkspaceRef(path="/repo"),
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
