from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentkit.providers.models import DoneEvent, TextEvent, ToolCallEvent
from agentkit.runtime import (
    BlockedOutcome,
    CompletedOutcome,
    RoundLimitOutcome,
    SegmentCoordinator,
)
from agentkit.runtime.contracts import (
    CommitRef,
    EffectReference,
    Initial,
    OperationStateVersion,
    RunSegmentRequest,
)
from coding_agent.approval import ApprovalPolicy
from coding_agent.executors.durable import LocalToolEffectBackend
from coding_agent.runtime_activation import RUNTIME_VERSION_NEW
from coding_agent.runs.serving_runtime import (
    DurableSegmentTurnAdapter,
    ProviderModelAdapter,
    _messages_from_model_request,
    admit_blocked_approval,
    approval_settlement_from_blocked,
    build_new_runtime_turn_adapter,
    initial_operation_state,
    session_model_adapter,
    session_serving_turn_kind,
    session_tool_executor,
    session_workspace_root,
    serving_tool_schemas,
)
from coding_agent.runs.turn_execution import DurableSegmentRunner
from coding_agent.server.session.models import _local_default_run_target
from coding_agent.server.stores.session_owner_store import OwnerAuthority
from coding_agent.stores.rtstore.harness import effect_status_may_replace
from tests.coding_agent.test_harness_p2_fact_source import (
    OWNER_ID,
    SESSION_ID,
    _open_store,
)


@dataclass
class _Session:
    runtime_version: str
    provider: object | None = None
    repo_path: Path | None = None
    default_run_target: object | None = None
    approval_policy: ApprovalPolicy = ApprovalPolicy.YOLO


class _StreamProvider:
    model_name = "stub"
    max_context_size = 1024
    tools: object = None

    async def stream(self, messages, tools=None, **kwargs):
        del messages, kwargs
        self.tools = tools
        yield TextEvent(text="done")
        yield DoneEvent()


class _ToolCallProvider:
    model_name = "stub"
    max_context_size = 1024
    tools: object = None

    async def stream(self, messages, tools=None, **kwargs):
        del messages, kwargs
        self.tools = tools
        if not tools:
            yield TextEvent(text="no-tools")
            yield DoneEvent()
            return
        yield ToolCallEvent(
            tool_call_id="call-1",
            name="file_read",
            arguments={"path": "README.md"},
        )
        yield DoneEvent()


class _TwoRoundProvider:
    model_name = "stub"
    max_context_size = 1024
    rounds = 0

    async def stream(self, messages, tools=None, **kwargs):
        del messages, tools, kwargs
        self.rounds += 1
        if self.rounds == 1:
            yield ToolCallEvent(
                tool_call_id="call-1",
                name="file_read",
                arguments={"path": "README.md"},
            )
            yield DoneEvent()
            return
        yield TextEvent(text="done")
        yield DoneEvent()


class _Probe:
    def observe(self) -> object:
        return object()

    async def wait(self, after: object) -> object:
        del after
        return self.observe()


class _Sink:
    async def emit(self, _item: object) -> None:
        return None


@dataclass
class _Outcome:
    final_message: str


class _Coordinator:
    async def run(
        self,
        request: object,
        control_probe: object,
        frame_sink: object,
        committed_fact_sink: object,
    ) -> _Outcome:
        del request, control_probe, frame_sink, committed_fact_sink
        return _Outcome(final_message="ok")


class _Port:
    def consume_authorization_replay_marker(self, request: object) -> None:
        del request
        return None

    async def recover_authorization_without_marker(self, request: object) -> None:
        del request
        return None


def test_session_serving_turn_kind_uses_session_version() -> None:
    assert session_serving_turn_kind(_Session(RUNTIME_VERSION_NEW)) == (
        "durable_segment_runner"
    )
    assert session_serving_turn_kind(_Session("legacy")) == "pipeline_adapter"


def test_generic_rank_does_not_treat_settled_as_completed() -> None:
    assert effect_status_may_replace(current="prepared", incoming="settled") is False
    assert effect_status_may_replace(current="settled", incoming="prepared") is False
    assert effect_status_may_replace(current="prepared", incoming="completed") is True


@pytest.mark.asyncio
async def test_durable_segment_turn_adapter_does_not_use_pipeline() -> None:
    adapter = DurableSegmentTurnAdapter(
        runner=DurableSegmentRunner(coordinator=_Coordinator(), commit_port=_Port()),
        request_for_prompt=lambda prompt: SimpleNamespace(max_rounds=1),
        control_probe=_Probe(),
        frame_sink=_Sink(),
        committed_fact_sink=_Sink(),
    )
    outcome = await adapter.run_turn("hello")
    assert isinstance(outcome, _Outcome)
    assert outcome.final_message == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_serving_factory_builds_coordinator_and_commit_port(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    adapter = build_new_runtime_turn_adapter(
        session=_Session(
            RUNTIME_VERSION_NEW,
            provider=_StreamProvider(),
            default_run_target=_local_default_run_target(tmp_path),
        ),
        run_id="run-serving",
        authority=owner,
        store=store,
        state_version=None,
    )
    assert isinstance(adapter, DurableSegmentTurnAdapter)
    assert isinstance(adapter.runner.coordinator, SegmentCoordinator)
    assert adapter.runner.commit_port is not None
    assert owner.session_id == SESSION_ID
    assert owner.owner_id == OWNER_ID
    assert isinstance(owner, OwnerAuthority)


def test_session_model_adapter_wraps_stream_provider(tmp_path: Path) -> None:
    executor = session_tool_executor(
        _Session(
            RUNTIME_VERSION_NEW,
            default_run_target=_local_default_run_target(tmp_path),
        )
    )
    adapter = session_model_adapter(
        _Session(RUNTIME_VERSION_NEW, provider=_StreamProvider()),
        executor,
    )
    assert isinstance(adapter, ProviderModelAdapter)


def test_session_model_adapter_rejects_missing_provider(tmp_path: Path) -> None:
    session = _Session(
        RUNTIME_VERSION_NEW,
        default_run_target=_local_default_run_target(tmp_path),
    )
    executor = session_tool_executor(session)

    with pytest.raises(
        RuntimeError,
        match="new-runtime serving requires a ModelAdapter",
    ):
        session_model_adapter(session, executor)


def test_session_workspace_root_uses_run_target(tmp_path: Path) -> None:
    target = _local_default_run_target(tmp_path)
    session = _Session(RUNTIME_VERSION_NEW, default_run_target=target)
    assert session_workspace_root(session) == tmp_path.resolve()
    executor = session_tool_executor(session)
    assert isinstance(LocalToolEffectBackend(executor), LocalToolEffectBackend)
    assert executor._workspace_root == tmp_path.resolve()


def test_messages_preserve_tool_call_history() -> None:
    request = SimpleNamespace(
        context={
            "messages": (
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": (
                        {
                            "tool_call_id": "call-1",
                            "name": "file_read",
                            "arguments": {"path": "README.md"},
                        },
                    ),
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "name": "file_read",
                    "content": {"text": "ok"},
                    "is_error": False,
                },
            )
        }
    )
    messages = _messages_from_model_request(request)
    assert messages[0]["tool_calls"][0]["id"] == "call-1"
    assert messages[0]["tool_calls"][0]["function"]["name"] == "file_read"
    assert messages[1]["role"] == "tool"
    assert messages[1]["tool_call_id"] == "call-1"
    assert messages[1]["content"] == '{"text": "ok"}'


@pytest.mark.asyncio
async def test_provider_stream_receives_core_tool_schemas(tmp_path: Path) -> None:
    provider = _ToolCallProvider()
    executor = session_tool_executor(
        _Session(
            RUNTIME_VERSION_NEW,
            default_run_target=_local_default_run_target(tmp_path),
        )
    )
    adapter = ProviderModelAdapter(
        provider,
        tools=serving_tool_schemas(executor),
        approval_policy=ApprovalPolicy.YOLO,
    )
    result = await adapter.generate(
        SimpleNamespace(request_id="req-1", context={}),
        _Sink(),
        None,
    )
    names = {schema.name for schema in provider.tools}
    assert "file_read" in names
    assert "subagent" not in names
    assert result.tool_calls[0].name == "file_read"
    assert result.tool_calls[0].requires_approval is False


@pytest.mark.asyncio
async def test_interactive_policy_marks_tool_calls_for_approval() -> None:
    adapter = ProviderModelAdapter(
        _ToolCallProvider(),
        tools=(SimpleNamespace(name="file_read"),),
        approval_policy=ApprovalPolicy.INTERACTIVE,
    )
    result = await adapter.generate(
        SimpleNamespace(request_id="req-1", context={}),
        _Sink(),
        None,
    )
    assert result.tool_calls[0].requires_approval is True
    assert result.tool_calls[0].approval_request_id == "req-1:call-1"


@pytest.mark.asyncio
async def test_auto_policy_allows_safe_file_read() -> None:
    adapter = ProviderModelAdapter(
        _ToolCallProvider(),
        tools=(SimpleNamespace(name="file_read"),),
        approval_policy=ApprovalPolicy.AUTO,
    )
    result = await adapter.generate(
        SimpleNamespace(request_id="req-1", context={}),
        _Sink(),
        None,
    )
    assert result.tool_calls[0].requires_approval is False
    assert result.tool_calls[0].approval_request_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_new_runtime_run_turn_reaches_completed_outcome(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    adapter = build_new_runtime_turn_adapter(
        session=_Session(
            RUNTIME_VERSION_NEW,
            provider=_StreamProvider(),
            default_run_target=_local_default_run_target(tmp_path),
        ),
        run_id="run-serving-turn",
        authority=owner,
        store=store,
        state_version=None,
    )
    outcome = await adapter.run_turn("hello")
    assert isinstance(outcome, CompletedOutcome)
    assert outcome.final_message == "done"
    assert outcome.stop_reason == "no_tool_calls"


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_interactive_tool_call_blocks_without_backend_execution(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    adapter = build_new_runtime_turn_adapter(
        session=_Session(
            RUNTIME_VERSION_NEW,
            provider=_ToolCallProvider(),
            default_run_target=_local_default_run_target(tmp_path),
            approval_policy=ApprovalPolicy.INTERACTIVE,
        ),
        run_id="run-serving-approval",
        authority=owner,
        store=store,
        state_version=None,
    )
    outcome = await adapter.run_turn("read the file")
    assert isinstance(outcome, BlockedOutcome)
    assert outcome.reason == "approval_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_interactive_allow_resumes_to_completed(
    store_kind: str,
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("hello from workspace\n", encoding="utf-8")
    store, owner = await _open_store(store_kind, tmp_path)
    provider = _TwoRoundProvider()

    async def resolve_blocked(blocked: BlockedOutcome, request: object) -> object:
        del request
        return await admit_blocked_approval(
            store,
            owner,
            blocked,
            approved=True,
        )

    adapter = build_new_runtime_turn_adapter(
        session=_Session(
            RUNTIME_VERSION_NEW,
            provider=provider,
            default_run_target=_local_default_run_target(tmp_path),
            approval_policy=ApprovalPolicy.INTERACTIVE,
        ),
        run_id="run-serving-allow",
        authority=owner,
        store=store,
        state_version=None,
        resolve_blocked=resolve_blocked,
    )
    outcome = await adapter.run_turn("read the file")
    assert isinstance(outcome, CompletedOutcome)
    assert outcome.final_message == "done"
    assert provider.rounds == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_interactive_deny_resumes_to_completed(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    provider = _TwoRoundProvider()

    async def resolve_blocked(blocked: BlockedOutcome, request: object) -> object:
        del request
        return await admit_blocked_approval(
            store,
            owner,
            blocked,
            approved=False,
        )

    adapter = build_new_runtime_turn_adapter(
        session=_Session(
            RUNTIME_VERSION_NEW,
            provider=provider,
            default_run_target=_local_default_run_target(tmp_path),
            approval_policy=ApprovalPolicy.INTERACTIVE,
        ),
        run_id="run-serving-deny",
        authority=owner,
        store=store,
        state_version=None,
        resolve_blocked=resolve_blocked,
    )
    outcome = await adapter.run_turn("read the file")
    assert isinstance(outcome, CompletedOutcome)
    assert outcome.final_message == "done"
    assert provider.rounds == 2


def _blocked_outcome(
    *,
    reason: str,
    effect_id: str = "effect-1",
    tool_call_id: str = "call-1",
    steps_taken: int = 1,
) -> BlockedOutcome:
    return BlockedOutcome(
        state_version=OperationStateVersion(
            run_id="run-indeterminate",
            revision=1,
            projection_epoch=0,
            commit_ref=CommitRef(transition_id="run-indeterminate:t1"),
            value={
                "_agentkit_runtime": {
                    "pending_effect_plans": [
                        {
                            "effect_id": effect_id,
                            "attempt_id": "attempt-1",
                            "effect_kind": "tool",
                            "payload": {
                                "tool_call_id": tool_call_id,
                                "tool_name": "file_read",
                                "arguments": {},
                            },
                            "requires_approval": True,
                        }
                    ]
                }
            },
        ),
        reason=reason,
        effect=EffectReference(
            effect_id=effect_id,
            attempt_id="attempt-1",
            tool_call_id=tool_call_id,
        ),
        steps_taken=steps_taken,
    )


class _BlockedCoordinator:
    def __init__(self, outcome: BlockedOutcome) -> None:
        self.outcome = outcome

    async def run(self, request, control_probe, frame_sink, committed_fact_sink):
        del request, control_probe, frame_sink, committed_fact_sink
        return self.outcome


@pytest.mark.asyncio
async def test_adapter_does_not_resume_indeterminate_block() -> None:
    blocked = _blocked_outcome(reason="indeterminate_dispatch")
    calls: list[str] = []

    async def resolve_blocked(outcome: BlockedOutcome, request: object) -> object:
        del request
        calls.append(outcome.reason)
        raise AssertionError("indeterminate blocks must not resume as approval")

    adapter = DurableSegmentTurnAdapter(
        runner=DurableSegmentRunner(
            coordinator=_BlockedCoordinator(blocked),
            commit_port=_Port(),
        ),
        request_for_prompt=lambda prompt: SimpleNamespace(max_rounds=1),
        control_probe=_Probe(),
        frame_sink=_Sink(),
        committed_fact_sink=_Sink(),
        resolve_blocked=resolve_blocked,
    )
    outcome = await adapter.run_turn("hello")
    assert outcome is blocked
    assert calls == []


class _NewRuntimeApprovalStore:
    def __init__(self) -> None:
        self.admitted: list[tuple[OwnerAuthority, object]] = []

    async def load_operation_state(
        self,
        session_id: str,
        run_id: str,
    ) -> None:
        del session_id, run_id
        return None

    async def admit_new_runtime_command(
        self,
        authority: OwnerAuthority,
        command: object,
    ) -> None:
        self.admitted.append((authority, command))


@pytest.mark.asyncio
async def test_new_runtime_http_approval_bypasses_legacy_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_build(**kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(resolve_blocked=kwargs.get("resolve_blocked"))

    monkeypatch.setattr(
        "coding_agent.runs.serving_runtime.build_new_runtime_turn_adapter",
        fake_build,
    )
    from coding_agent.server.session.manager import SessionManager

    manager = SessionManager()
    session_id = await manager.create_session()
    session = await manager.get_session_async(session_id)
    session.runtime_version = RUNTIME_VERSION_NEW
    session.current_turn_id = "run-indeterminate"
    store = _NewRuntimeApprovalStore()
    owner = OwnerAuthority(session_id=session_id, owner_id="owner-1", epoch=1)
    monkeypatch.setattr(manager, "_authoritative_store", lambda: store)
    manager._owner_authorities[session_id] = owner

    def fail_legacy_consumer(_session: object) -> object:
        raise AssertionError("new-runtime approval must not use the legacy consumer")

    monkeypatch.setattr(
        manager,
        "_make_session_consumer",
        fail_legacy_consumer,
    )

    adapter = await manager._build_new_runtime_turn_adapter(
        session,
        SimpleNamespace(run_id="run-indeterminate"),
    )
    blocked = _blocked_outcome(reason="approval_required")
    resolver = adapter.resolve_blocked
    assert resolver is not None
    resolution = asyncio.create_task(resolver(blocked, SimpleNamespace(max_rounds=5)))
    for _ in range(10):
        if session.approval_coordinator.get_request("call-1") is not None:
            break
        if resolution.done():
            await resolution
        await asyncio.sleep(0)
    assert session.approval_coordinator.get_request("call-1") is not None

    response = await manager.submit_approval_response(
        session_id=session_id,
        request_id="call-1",
        approved=True,
    )
    assert len(store.admitted) == 1
    settlement = await resolution

    assert response is not None
    assert response.approved is True
    assert settlement.approved is True
    assert len(store.admitted) == 1
    admitted_authority, command = store.admitted[0]
    assert admitted_authority == owner
    assert command.command_kind == "approval_decision"
    assert command.payload == {
        "approved": True,
        "request_id": settlement.input_id,
        "target_run_id": "run-indeterminate",
    }


def test_approval_command_id_includes_request_identity() -> None:
    first = approval_settlement_from_blocked(
        _blocked_outcome(
            reason="approval_required",
            effect_id="effect-a",
            tool_call_id="call-a",
        ),
        approved=True,
        owner_epoch=1,
    )
    second = approval_settlement_from_blocked(
        _blocked_outcome(
            reason="approval_required",
            effect_id="effect-b",
            tool_call_id="call-b",
        ),
        approved=True,
        owner_epoch=1,
    )
    assert first.command_id != second.command_id
    assert "call-a" in first.command_id
    assert "call-b" in second.command_id


class _BudgetCoordinator:
    def __init__(self) -> None:
        self.max_rounds: list[int] = []

    async def run(self, request, control_probe, frame_sink, committed_fact_sink):
        del control_probe, frame_sink, committed_fact_sink
        self.max_rounds.append(request.max_rounds)
        if len(self.max_rounds) == 1:
            return _blocked_outcome(
                reason="approval_required",
                steps_taken=2,
            )
        return CompletedOutcome(
            state_version=request.state_version,
            final_message="done",
            steps_taken=1,
            stop_reason="no_tool_calls",
        )


@pytest.mark.asyncio
async def test_approval_resume_uses_remaining_round_budget() -> None:
    coordinator = _BudgetCoordinator()

    async def resolve_blocked(blocked: BlockedOutcome, request: object) -> object:
        del request
        return approval_settlement_from_blocked(
            blocked,
            approved=True,
            owner_epoch=1,
        )

    def request_for_prompt(prompt: str) -> RunSegmentRequest:
        del prompt
        return RunSegmentRequest(
            session_id="session-1",
            owner_id="owner-1",
            owner_epoch=1,
            state_version=initial_operation_state("run-budget"),
            step_input=Initial(
                input_id="run-budget:initial",
                command_batch=(),
                mailbox_cut=0,
            ),
            max_rounds=5,
        )

    adapter = DurableSegmentTurnAdapter(
        runner=DurableSegmentRunner(coordinator=coordinator, commit_port=_Port()),
        request_for_prompt=request_for_prompt,
        control_probe=_Probe(),
        frame_sink=_Sink(),
        committed_fact_sink=_Sink(),
        resolve_blocked=resolve_blocked,
    )
    outcome = await adapter.run_turn("hello")
    assert isinstance(outcome, CompletedOutcome)
    assert coordinator.max_rounds == [5, 3]
    assert outcome.steps_taken == 3


class _ExhaustedBudgetCoordinator:
    async def run(self, request, control_probe, frame_sink, committed_fact_sink):
        del control_probe, frame_sink, committed_fact_sink
        return _blocked_outcome(
            reason="approval_required",
            steps_taken=request.max_rounds,
        )


@pytest.mark.asyncio
async def test_exhausted_approval_wait_returns_round_limit() -> None:
    resolver_called = False

    async def resolve_blocked(blocked: BlockedOutcome, request: object) -> object:
        del blocked, request
        nonlocal resolver_called
        resolver_called = True
        raise AssertionError("exhausted turns must not wait for approval")

    adapter = DurableSegmentTurnAdapter(
        runner=DurableSegmentRunner(
            coordinator=_ExhaustedBudgetCoordinator(),
            commit_port=_Port(),
        ),
        request_for_prompt=lambda prompt: RunSegmentRequest(
            session_id="session-1",
            owner_id="owner-1",
            owner_epoch=1,
            state_version=initial_operation_state("run-budget"),
            step_input=Initial(
                input_id=f"{prompt}:initial",
                command_batch=(),
                mailbox_cut=0,
            ),
            max_rounds=2,
        ),
        control_probe=_Probe(),
        frame_sink=_Sink(),
        committed_fact_sink=_Sink(),
        resolve_blocked=resolve_blocked,
    )

    outcome = await adapter.run_turn("hello")

    assert isinstance(outcome, RoundLimitOutcome)
    assert outcome.steps_taken == 2
    assert resolver_called is False
