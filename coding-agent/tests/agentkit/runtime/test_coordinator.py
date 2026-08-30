from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

import pytest

from agentkit.runtime import (
    AgentEngine,
    ApprovalResolved,
    ApprovalSettlement,
    BlockedOutcome,
    CommitRef,
    CommitSettlementRequest,
    CommittedCommitResult,
    CommittedFactNotice,
    CompletedOutcome,
    ControlGeneration,
    ControlSnapshot,
    DispatchAuthorizationRequest,
    DispatchAuthorizedResult,
    DispatchPermit,
    EffectCompletedResult,
    EffectFailedResult,
    EffectMutation,
    EffectSettled,
    EffectSettlement,
    EffectSettlementOutcome,
    EffectStatus,
    EngineStepRequest,
    FailureReport,
    FailedOutcome,
    Initial,
    ModelGenerationResult,
    ModelToolCall,
    ModelUsage,
    OperationStateVersion,
    PreparedEffectAction,
    ProviderStopMetadata,
    RoundLimitOutcome,
    RunSegmentRequest,
    SafeYieldOutcome,
    SegmentCoordinator,
    StreamFrame,
    StreamFrameKind,
)


def _initial_state(*, value: dict[str, object] | None = None) -> OperationStateVersion:
    return OperationStateVersion(
        run_id="run-1",
        revision=0,
        projection_epoch=2,
        commit_ref=CommitRef(transition_id="admission"),
        value=value or {},
    )


def _initial_request(*, max_rounds: int = 4) -> RunSegmentRequest:
    return RunSegmentRequest(
        session_id="session-1",
        owner_id="owner-1",
        owner_epoch=3,
        state_version=_initial_state(),
        step_input=Initial(input_id="initial-1", command_batch=(), mailbox_cut=11),
        max_rounds=max_rounds,
    )


def _model_result(
    result_id: str,
    *,
    content: str,
    tool_calls: Iterable[ModelToolCall] = (),
) -> ModelGenerationResult:
    calls = tuple(tool_calls)
    return ModelGenerationResult(
        result_id=result_id,
        request_id=f"request-{result_id}",
        assistant_content=content,
        finalized_thinking=None,
        tool_calls=calls,
        usage=ModelUsage(input_tokens=4, output_tokens=2),
        provider_stop=ProviderStopMetadata(reason="tool_use" if calls else "stop"),
    )


def _tool_call(*, approval: bool = False) -> ModelToolCall:
    return ModelToolCall(
        tool_call_id="call-1",
        name="read",
        arguments={"path": "README.md"},
        requires_approval=approval,
        approval_request_id="approval-1" if approval else None,
    )


class RecordingCommitPort:
    def __init__(self, timeline: list[str] | None = None) -> None:
        self.timeline = timeline if timeline is not None else []
        self.transition_requests: list[Any] = []
        self.authorization_requests: list[DispatchAuthorizationRequest] = []
        self.settlement_requests: list[Any] = []
        self.reconciliation_requests: list[Any] = []

    @staticmethod
    def _notices(
        request: Any, state: OperationStateVersion
    ) -> tuple[CommittedFactNotice, ...]:
        return tuple(
            CommittedFactNotice(
                fact_id=fact.fact_id,
                fact_kind=fact.fact_kind,
                payload=fact.payload,
                session_seq=str(index),
                projection_epoch=state.projection_epoch,
                event_record_id=f"event-{fact.fact_id}",
            )
            for index, fact in enumerate(request.proposal.pending_facts, start=1)
        )

    @staticmethod
    def _state(request: Any) -> OperationStateVersion:
        previous = request.engine_request.state_version
        return OperationStateVersion(
            run_id=previous.run_id,
            revision=previous.revision + 1,
            projection_epoch=previous.projection_epoch,
            commit_ref=CommitRef(transition_id=request.proposal.transition_id),
            value=request.proposal.state_value,
        )

    async def commit_transition(self, request: Any):
        self.timeline.append("commit_transition")
        self.transition_requests.append(request)
        state = self._state(request)
        return CommittedCommitResult(
            state_version=state,
            notices=self._notices(request, state),
        )

    async def authorize_dispatch(self, request: DispatchAuthorizationRequest):
        self.timeline.append("authorize_dispatch")
        self.authorization_requests.append(request)
        state = OperationStateVersion(
            run_id=request.engine_request.state_version.run_id,
            revision=request.engine_request.state_version.revision + 1,
            projection_epoch=request.engine_request.state_version.projection_epoch,
            commit_ref=CommitRef(transition_id=request.proposal.transition_id),
            value=request.proposal.state_value,
        )
        return DispatchAuthorizedResult(
            state_version=state,
            permit=DispatchPermit.issue(
                opaque_token=f"permit-{request.effect_plan.attempt_id}",
                session_id=request.session_id,
                effect_id=request.effect_plan.effect_id,
                attempt_id=request.effect_plan.attempt_id,
                authorization_transition_id=request.proposal.transition_id,
                owner_epoch=request.owner_epoch,
                idempotency_key=request.effect_plan.idempotency_key,
            ),
            notices=self._notices(request, state),
        )

    async def commit_settlement(self, request: Any):
        self.timeline.append("commit_settlement")
        self.settlement_requests.append(request)
        state = self._state(request)
        return CommittedCommitResult(
            state_version=state,
            notices=self._notices(request, state),
        )

    async def commit_reconciliation(self, request: Any):
        self.timeline.append("commit_reconciliation")
        self.reconciliation_requests.append(request)
        raise AssertionError("reconciliation is not used by these scenarios")


class SequenceModelAdapter:
    def __init__(
        self,
        results: Iterable[ModelGenerationResult],
        timeline: list[str] | None = None,
        *,
        frame: StreamFrame | None = None,
        echo_request_id: bool = True,
    ) -> None:
        self.results = list(results)
        self.timeline = timeline if timeline is not None else []
        self.frame = frame
        self.echo_request_id = echo_request_id
        self.calls: list[tuple[Any, Any]] = []

    async def generate(self, request, frame_sink, cancellation):
        self.timeline.append("model_generate")
        self.calls.append((request, cancellation))
        if self.frame is not None:
            await frame_sink.emit(self.frame)
        if not self.results:
            raise AssertionError("unexpected model generation")
        result = self.results.pop(0)
        if self.echo_request_id:
            return replace(result, request_id=request.request_id)
        return result


class RecordingEffectExecutor:
    def __init__(self, timeline: list[str] | None = None) -> None:
        self.timeline = timeline if timeline is not None else []
        self.calls: list[tuple[DispatchPermit, Any]] = []

    async def execute(self, permit, cancellation):
        self.timeline.append("effect_execute")
        self.calls.append((permit, cancellation))
        return EffectCompletedResult(result={"content": "contents"})


class QuietControlProbe:
    def __init__(self, timeline: list[str] | None = None) -> None:
        self.timeline = timeline if timeline is not None else []
        self.observe_count = 0
        self._never = asyncio.Event()

    def observe(self) -> ControlSnapshot:
        self.timeline.append("control_observe")
        self.observe_count += 1
        return ControlSnapshot(
            generation=ControlGeneration(self.observe_count),
            raised=False,
        )

    async def wait(self, after: ControlGeneration) -> ControlSnapshot:
        del after
        await self._never.wait()
        raise AssertionError("unreachable")


class RecordingFrameSink:
    def __init__(self, timeline: list[str] | None = None) -> None:
        self.timeline = timeline if timeline is not None else []
        self.frames: list[StreamFrame] = []

    async def emit(self, frame: StreamFrame) -> None:
        self.timeline.append("frame_emit")
        self.frames.append(frame)


class RecordingFactSink:
    def __init__(self, timeline: list[str] | None = None) -> None:
        self.timeline = timeline if timeline is not None else []
        self.notices: list[CommittedFactNotice] = []

    async def emit(self, notice: CommittedFactNotice) -> None:
        self.timeline.append(f"notice:{notice.fact_kind}")
        self.notices.append(notice)


def _coordinator(
    model_adapter,
    commit_port,
    effect_executor,
) -> SegmentCoordinator:
    return SegmentCoordinator(
        engine=AgentEngine(),
        model_adapter=model_adapter,
        commit_port=commit_port,
        effect_executor=effect_executor,
    )


@pytest.mark.asyncio
async def test_model_adapter_is_host_provided_and_distinct_from_effect_executor() -> (
    None
):
    model = SequenceModelAdapter([_model_result("result-1", content="done")])
    executor = RecordingEffectExecutor()
    coordinator = _coordinator(model, RecordingCommitPort(), executor)

    outcome = await coordinator.run(
        _initial_request(),
        control_probe=QuietControlProbe(),
        frame_sink=RecordingFrameSink(),
        committed_fact_sink=RecordingFactSink(),
    )

    assert isinstance(outcome, CompletedOutcome)
    assert len(model.calls) == 1
    assert executor.calls == []
    assert model is not executor


@pytest.mark.asyncio
async def test_model_adapter_generate_returns_model_generation_result() -> None:
    expected = _model_result("result-1", content="done")
    model = SequenceModelAdapter([expected])
    coordinator = _coordinator(model, RecordingCommitPort(), RecordingEffectExecutor())

    outcome = await coordinator.run(
        _initial_request(),
        control_probe=QuietControlProbe(),
        frame_sink=RecordingFrameSink(),
        committed_fact_sink=RecordingFactSink(),
    )

    assert isinstance(outcome, CompletedOutcome)
    assert outcome.final_message == "done"


@pytest.mark.asyncio
async def test_wrong_model_request_id_fails_without_result_transition_commit() -> None:
    commits = RecordingCommitPort()
    model = SequenceModelAdapter(
        [_model_result("wrong-request", content="must not commit")],
        echo_request_id=False,
    )
    coordinator = _coordinator(model, commits, RecordingEffectExecutor())

    outcome = await coordinator.run(
        _initial_request(),
        control_probe=QuietControlProbe(),
        frame_sink=RecordingFrameSink(),
        committed_fact_sink=RecordingFactSink(),
    )

    assert isinstance(outcome, FailedOutcome)
    assert outcome.error.code == "model_request_id_mismatch"
    assert outcome.error.message
    assert len(commits.transition_requests) == 1
    assert commits.transition_requests[0].proposal.pending_facts == ()
    assert commits.authorization_requests == []
    assert commits.settlement_requests == []


@pytest.mark.asyncio
async def test_segment_coordinator_sequences_commit_port_and_effect_executor() -> None:
    timeline: list[str] = []
    model = SequenceModelAdapter(
        [
            _model_result("result-tool", content="", tool_calls=(_tool_call(),)),
            _model_result("result-done", content="done"),
        ],
        timeline,
    )
    commits = RecordingCommitPort(timeline)
    executor = RecordingEffectExecutor(timeline)
    coordinator = _coordinator(model, commits, executor)

    outcome = await coordinator.run(
        _initial_request(),
        control_probe=QuietControlProbe(timeline),
        frame_sink=RecordingFrameSink(timeline),
        committed_fact_sink=RecordingFactSink(timeline),
    )

    assert isinstance(outcome, CompletedOutcome)
    prepared_index = timeline.index(
        "commit_transition", timeline.index("model_generate") + 1
    )
    authorization_index = timeline.index("authorize_dispatch")
    execute_index = timeline.index("effect_execute")
    settlement_index = timeline.index("commit_settlement")
    assert prepared_index < authorization_index < execute_index < settlement_index
    assert len(executor.calls) == 1


@pytest.mark.asyncio
async def test_segment_coordinator_owns_loop_and_settlement_reentry_until_limit() -> (
    None
):
    model = SequenceModelAdapter(
        [_model_result("result-tool", content="", tool_calls=(_tool_call(),))]
    )
    commits = RecordingCommitPort()
    executor = RecordingEffectExecutor()
    coordinator = _coordinator(model, commits, executor)

    outcome = await coordinator.run(
        _initial_request(max_rounds=1),
        control_probe=QuietControlProbe(),
        frame_sink=RecordingFrameSink(),
        committed_fact_sink=RecordingFactSink(),
    )

    assert isinstance(outcome, RoundLimitOutcome)
    assert outcome.steps_taken == 1
    assert len(commits.settlement_requests) == 1
    assert len(executor.calls) == 1


@pytest.mark.asyncio
async def test_frame_sink_emit_await_bounds_backpressure() -> None:
    release = asyncio.Event()
    emitted = asyncio.Event()

    class BlockingSink:
        async def emit(self, frame: StreamFrame) -> None:
            del frame
            emitted.set()
            await release.wait()

    frame = StreamFrame(
        frame_id="frame-1",
        kind=StreamFrameKind.TOKEN_DELTA,
        payload={"text": "partial"},
    )
    coordinator = _coordinator(
        SequenceModelAdapter([_model_result("result-1", content="done")], frame=frame),
        RecordingCommitPort(),
        RecordingEffectExecutor(),
    )
    run_task = asyncio.create_task(
        coordinator.run(
            _initial_request(),
            control_probe=QuietControlProbe(),
            frame_sink=BlockingSink(),
            committed_fact_sink=RecordingFactSink(),
        )
    )

    await emitted.wait()
    assert not run_task.done()
    release.set()
    assert isinstance(await run_task, CompletedOutcome)


@pytest.mark.asyncio
async def test_frame_sink_failure_disables_ephemeral_frames_without_failing_segment() -> (
    None
):
    class FailingSink:
        def __init__(self) -> None:
            self.calls = 0

        async def emit(self, frame: StreamFrame) -> None:
            del frame
            self.calls += 1
            raise RuntimeError()

    frame = StreamFrame(
        frame_id="frame-1",
        kind=StreamFrameKind.TOKEN_DELTA,
        payload={"text": "partial"},
    )
    sink = FailingSink()
    coordinator = _coordinator(
        SequenceModelAdapter([_model_result("result-1", content="done")], frame=frame),
        RecordingCommitPort(),
        RecordingEffectExecutor(),
    )

    outcome = await coordinator.run(
        _initial_request(),
        control_probe=QuietControlProbe(),
        frame_sink=sink,
        committed_fact_sink=RecordingFactSink(),
    )

    assert isinstance(outcome, CompletedOutcome)
    assert sink.calls == 1
    assert coordinator.delivery_failures[0].sink == "frame"
    assert coordinator.delivery_failures[0].message == "RuntimeError"


@pytest.mark.asyncio
async def test_control_probe_polled_at_all_safe_points() -> None:
    probe = QuietControlProbe()
    coordinator = _coordinator(
        SequenceModelAdapter(
            [
                _model_result("result-tool", content="", tool_calls=(_tool_call(),)),
                _model_result("result-done", content="done"),
            ]
        ),
        RecordingCommitPort(),
        RecordingEffectExecutor(),
    )

    outcome = await coordinator.run(
        _initial_request(),
        control_probe=probe,
        frame_sink=RecordingFrameSink(),
        committed_fact_sink=RecordingFactSink(),
    )

    assert isinstance(outcome, CompletedOutcome)
    assert probe.observe_count >= 8


@pytest.mark.asyncio
async def test_raised_probe_causes_safe_yield_and_command_remains_pending() -> None:
    class RaisedProbe(QuietControlProbe):
        def observe(self) -> ControlSnapshot:
            self.observe_count += 1
            return ControlSnapshot(
                generation=ControlGeneration(self.observe_count),
                raised=True,
                reason="interrupt",
            )

    commits = RecordingCommitPort()
    coordinator = _coordinator(
        SequenceModelAdapter([]),
        commits,
        RecordingEffectExecutor(),
    )

    outcome = await coordinator.run(
        _initial_request(),
        control_probe=RaisedProbe(),
        frame_sink=RecordingFrameSink(),
        committed_fact_sink=RecordingFactSink(),
    )

    assert isinstance(outcome, SafeYieldOutcome)
    assert outcome.reason == "interrupt"
    assert commits.transition_requests == []
    assert _initial_request().step_input.command_batch == ()


@pytest.mark.asyncio
async def test_control_probe_cancellation_during_generate_discards_uncommitted_partial_output_and_returns_safe_yield() -> (
    None
):
    partial_emitted = asyncio.Event()

    class InterruptingProbe(QuietControlProbe):
        async def wait(self, after: ControlGeneration) -> ControlSnapshot:
            del after
            await partial_emitted.wait()
            return ControlSnapshot(
                generation=ControlGeneration(100),
                raised=True,
                reason="interrupt",
            )

    class CancellableModel:
        async def generate(self, request, frame_sink, cancellation):
            del request
            await frame_sink.emit(
                StreamFrame(
                    frame_id="partial-1",
                    kind=StreamFrameKind.TOKEN_DELTA,
                    payload={"text": "uncommitted"},
                )
            )
            partial_emitted.set()
            await cancellation.wait()
            return _model_result("discarded", content="must not commit")

    commits = RecordingCommitPort()
    coordinator = _coordinator(CancellableModel(), commits, RecordingEffectExecutor())

    outcome = await coordinator.run(
        _initial_request(),
        control_probe=InterruptingProbe(),
        frame_sink=RecordingFrameSink(),
        committed_fact_sink=RecordingFactSink(),
    )

    assert isinstance(outcome, SafeYieldOutcome)
    assert all(
        not request.proposal.pending_facts for request in commits.transition_requests
    )


@pytest.mark.asyncio
async def test_cancellation_token_from_control_probe_stops_live_model_stream() -> None:
    cancellation_observed = asyncio.Event()

    class InterruptingProbe(QuietControlProbe):
        async def wait(self, after: ControlGeneration) -> ControlSnapshot:
            del after
            return ControlSnapshot(
                generation=ControlGeneration(99),
                raised=True,
                reason="interrupt",
            )

    class WaitingModel:
        async def generate(self, request, frame_sink, cancellation):
            del request, frame_sink
            await cancellation.wait()
            cancellation_observed.set()
            return _model_result("discarded", content="discarded")

    coordinator = _coordinator(
        WaitingModel(), RecordingCommitPort(), RecordingEffectExecutor()
    )
    outcome = await coordinator.run(
        _initial_request(),
        control_probe=InterruptingProbe(),
        frame_sink=RecordingFrameSink(),
        committed_fact_sink=RecordingFactSink(),
    )

    assert isinstance(outcome, SafeYieldOutcome)
    assert cancellation_observed.is_set()


@pytest.mark.asyncio
async def test_approval_wait_allocates_effect_id_commits_prepared_and_yields_blocked_without_permit() -> (
    None
):
    commits = RecordingCommitPort()
    executor = RecordingEffectExecutor()
    coordinator = _coordinator(
        SequenceModelAdapter(
            [
                _model_result(
                    "result-approval",
                    content="",
                    tool_calls=(_tool_call(approval=True),),
                )
            ]
        ),
        commits,
        executor,
    )

    outcome = await coordinator.run(
        _initial_request(),
        control_probe=QuietControlProbe(),
        frame_sink=RecordingFrameSink(),
        committed_fact_sink=RecordingFactSink(),
    )

    assert isinstance(outcome, BlockedOutcome)
    assert outcome.effect is not None
    assert outcome.effect.effect_id
    assert outcome.effect.attempt_id
    prepared = commits.transition_requests[-1].proposal
    assert prepared.effect_plans[0].effect_id == outcome.effect.effect_id
    assert [fact.fact_kind for fact in prepared.pending_facts][-2:] == [
        "tool_call",
        "approval_requested",
    ]
    assert commits.authorization_requests == []
    assert executor.calls == []


async def _blocked_approval_state() -> tuple[BlockedOutcome, RecordingCommitPort]:
    commits = RecordingCommitPort()
    coordinator = _coordinator(
        SequenceModelAdapter(
            [
                _model_result(
                    "result-approval",
                    content="",
                    tool_calls=(_tool_call(approval=True),),
                )
            ]
        ),
        commits,
        RecordingEffectExecutor(),
    )
    outcome = await coordinator.run(
        _initial_request(),
        control_probe=QuietControlProbe(),
        frame_sink=RecordingFrameSink(),
        committed_fact_sink=RecordingFactSink(),
    )
    assert isinstance(outcome, BlockedOutcome)
    return outcome, commits


def _approval_settlement(
    blocked: BlockedOutcome,
    *,
    approved: bool,
    owner_epoch: int = 3,
    transition_id: str | None = None,
    tool_call_id: str = "call-1",
    tool_name: str = "read",
) -> ApprovalSettlement:
    assert blocked.effect is not None
    owning_transition_id = transition_id
    if owning_transition_id is None:
        owning_transition_id = blocked.state_version.commit_ref.transition_id
    return ApprovalSettlement(
        input_id="approval-input-1",
        command_id="approval-command-1",
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        effect_id=blocked.effect.effect_id,
        attempt_id=blocked.effect.attempt_id,
        transition_id=owning_transition_id,
        owner_epoch=owner_epoch,
        approved=approved,
        rejection_reason_code=None if approved else "user_denied",
        rejection_message=None if approved else "User denied this tool call",
    )


def _approval_run_request(
    blocked: BlockedOutcome,
    *,
    approved: bool,
) -> RunSegmentRequest:
    settlement = _approval_settlement(blocked, approved=approved)
    return RunSegmentRequest(
        session_id="session-1",
        owner_id="owner-1",
        owner_epoch=3,
        state_version=blocked.state_version,
        step_input=ApprovalResolved(settlement=settlement),
        max_rounds=2,
    )


@pytest.mark.asyncio
async def test_run_segment_rejects_old_owner_approval_before_engine_or_port() -> None:
    blocked, commits = await _blocked_approval_state()
    transition_count = len(commits.transition_requests)
    stale = _approval_settlement(
        blocked,
        approved=True,
        owner_epoch=2,
    )

    with pytest.raises(ValueError, match="owner_epoch"):
        RunSegmentRequest(
            session_id="session-1",
            owner_id="owner-1",
            owner_epoch=3,
            state_version=blocked.state_version,
            step_input=ApprovalResolved(settlement=stale),
            max_rounds=2,
        )
    assert len(commits.transition_requests) == transition_count
    assert commits.authorization_requests == []
    assert commits.settlement_requests == []


@pytest.mark.asyncio
async def test_run_segment_rejects_wrong_effect_transition_before_engine_or_port() -> (
    None
):
    blocked, commits = await _blocked_approval_state()
    transition_count = len(commits.transition_requests)
    assert blocked.effect is not None
    settlement = EffectSettlement.completed(
        input_id="effect-input-wrong-transition",
        tool_call_id="call-1",
        tool_name="read",
        effect_id=blocked.effect.effect_id,
        attempt_id=blocked.effect.attempt_id,
        authorization_transition_id="wrong-transition",
        owner_epoch=3,
        result={"content": "must not commit"},
    )

    with pytest.raises(ValueError, match="authorization_transition_id"):
        RunSegmentRequest(
            session_id="session-1",
            owner_id="owner-1",
            owner_epoch=3,
            state_version=blocked.state_version,
            step_input=EffectSettled(settlement=settlement),
            max_rounds=2,
        )
    assert len(commits.transition_requests) == transition_count
    assert commits.authorization_requests == []
    assert commits.settlement_requests == []


@pytest.mark.asyncio
async def test_run_segment_rejects_mismatched_approval_tool_before_engine_or_port() -> (
    None
):
    blocked, commits = await _blocked_approval_state()
    transition_count = len(commits.transition_requests)
    mismatched = _approval_settlement(
        blocked,
        approved=False,
        tool_name="write",
    )

    with pytest.raises(ValueError, match="tool_name"):
        RunSegmentRequest(
            session_id="session-1",
            owner_id="owner-1",
            owner_epoch=3,
            state_version=blocked.state_version,
            step_input=ApprovalResolved(settlement=mismatched),
            max_rounds=2,
        )
    assert len(commits.transition_requests) == transition_count
    assert commits.authorization_requests == []
    assert commits.settlement_requests == []


@pytest.mark.asyncio
async def test_commit_settlement_request_rejects_old_owner() -> None:
    blocked, _ = await _blocked_approval_state()
    valid = _approval_settlement(blocked, approved=False)
    engine_request = EngineStepRequest(
        state_version=blocked.state_version,
        step_input=ApprovalResolved(settlement=valid),
    )
    proposal = AgentEngine().propose(engine_request)
    stale = replace(valid, owner_epoch=2)

    with pytest.raises(ValueError, match="owner_epoch"):
        CommitSettlementRequest(
            session_id="session-1",
            owner_id="owner-1",
            owner_epoch=3,
            engine_request=engine_request,
            proposal=proposal,
            settlement=stale,
            effect_mutation=EffectMutation(
                effect_id=stale.effect_id,
                attempt_id=stale.attempt_id,
                expected_status=EffectStatus.PREPARED,
                status=EffectStatus.REJECTED,
                payload={"reason_code": "user_denied"},
            ),
        )


@pytest.mark.asyncio
async def test_dispatch_authorization_rejects_mismatched_approval_tool_identity() -> (
    None
):
    blocked, _ = await _blocked_approval_state()
    valid = _approval_settlement(blocked, approved=True)
    engine_request = EngineStepRequest(
        state_version=blocked.state_version,
        step_input=ApprovalResolved(settlement=valid),
    )
    proposal = AgentEngine().propose(engine_request)
    assert isinstance(proposal.next_action, PreparedEffectAction)
    plan = proposal.next_action.effect_plan
    mismatched = replace(valid, tool_call_id="other-call")

    with pytest.raises(ValueError, match="tool_call_id"):
        DispatchAuthorizationRequest(
            session_id="session-1",
            owner_id="owner-1",
            owner_epoch=3,
            mailbox_cut=11,
            engine_request=engine_request,
            proposal=proposal,
            effect_plan=plan,
            effect_mutation=EffectMutation(
                effect_id=plan.effect_id,
                attempt_id=plan.attempt_id,
                expected_status=EffectStatus.PREPARED,
                status=EffectStatus.DISPATCHED,
                payload={"authorization_transition_id": proposal.transition_id},
            ),
            approval_settlement=mismatched,
        )


@pytest.mark.asyncio
async def test_approved_response_dispositions_prepared_to_dispatched_before_permit_issue() -> (
    None
):
    blocked, _ = await _blocked_approval_state()
    timeline: list[str] = []
    commits = RecordingCommitPort(timeline)
    executor = RecordingEffectExecutor(timeline)
    coordinator = _coordinator(
        SequenceModelAdapter([_model_result("done", content="done")], timeline),
        commits,
        executor,
    )

    outcome = await coordinator.run(
        _approval_run_request(blocked, approved=True),
        control_probe=QuietControlProbe(timeline),
        frame_sink=RecordingFrameSink(timeline),
        committed_fact_sink=RecordingFactSink(timeline),
    )

    assert isinstance(outcome, CompletedOutcome)
    assert timeline.index("authorize_dispatch") < timeline.index("effect_execute")
    authorization = commits.authorization_requests[0]
    assert authorization.approval_settlement is not None
    assert authorization.proposal.dispositions[0].command_id == "approval-command-1"
    assert authorization.effect_mutation.expected_status.value == "prepared"
    assert authorization.effect_mutation.status.value == "dispatched"
    assert executor.calls[0][0].claimed is True


@pytest.mark.asyncio
async def test_rejected_response_dispositions_prepared_to_rejected_and_no_permit_exists() -> (
    None
):
    blocked, _ = await _blocked_approval_state()
    commits = RecordingCommitPort()
    executor = RecordingEffectExecutor()
    coordinator = _coordinator(
        SequenceModelAdapter([_model_result("done", content="done")]),
        commits,
        executor,
    )

    outcome = await coordinator.run(
        _approval_run_request(blocked, approved=False),
        control_probe=QuietControlProbe(),
        frame_sink=RecordingFrameSink(),
        committed_fact_sink=RecordingFactSink(),
    )

    assert isinstance(outcome, CompletedOutcome)
    assert commits.authorization_requests == []
    assert executor.calls == []
    assert (
        commits.settlement_requests[0].proposal.dispositions[0].reason_code
        == "user_denied"
    )


@pytest.mark.asyncio
async def test_commit_requests_assign_approval_denial_fact_disposition_and_effect_mutation_to_one_atomic_commit() -> (
    None
):
    blocked, _ = await _blocked_approval_state()
    commits = RecordingCommitPort()
    coordinator = _coordinator(
        SequenceModelAdapter([_model_result("done", content="done")]),
        commits,
        RecordingEffectExecutor(),
    )

    await coordinator.run(
        _approval_run_request(blocked, approved=False),
        control_probe=QuietControlProbe(),
        frame_sink=RecordingFrameSink(),
        committed_fact_sink=RecordingFactSink(),
    )

    denial_commits = [
        request
        for request in commits.settlement_requests
        if isinstance(request.settlement, ApprovalSettlement)
    ]
    assert len(denial_commits) == 1
    denial = denial_commits[0]
    assert [fact.fact_kind for fact in denial.proposal.pending_facts] == ["tool_result"]
    assert len(denial.proposal.dispositions) == 1
    assert denial.effect_mutation.status.value == "rejected"


@pytest.mark.asyncio
async def test_approval_denial_commits_exactly_one_tool_result_and_reaches_next_model_round_without_permit() -> (
    None
):
    blocked, _ = await _blocked_approval_state()
    commits = RecordingCommitPort()
    facts = RecordingFactSink()
    executor = RecordingEffectExecutor()
    coordinator = _coordinator(
        SequenceModelAdapter([_model_result("done", content="continued")]),
        commits,
        executor,
    )

    outcome = await coordinator.run(
        _approval_run_request(blocked, approved=False),
        control_probe=QuietControlProbe(),
        frame_sink=RecordingFrameSink(),
        committed_fact_sink=facts,
    )

    assert isinstance(outcome, CompletedOutcome)
    assert (
        len([notice for notice in facts.notices if notice.fact_kind == "tool_result"])
        == 1
    )
    assert executor.calls == []


@pytest.mark.asyncio
async def test_committed_fact_notice_emitted_only_after_prepared_and_settlement_commits() -> (
    None
):
    timeline: list[str] = []
    coordinator = _coordinator(
        SequenceModelAdapter(
            [
                _model_result("tool", content="", tool_calls=(_tool_call(),)),
                _model_result("done", content="done"),
            ],
            timeline,
        ),
        RecordingCommitPort(timeline),
        RecordingEffectExecutor(timeline),
    )

    await coordinator.run(
        _initial_request(),
        control_probe=QuietControlProbe(timeline),
        frame_sink=RecordingFrameSink(timeline),
        committed_fact_sink=RecordingFactSink(timeline),
    )

    tool_call_notice = timeline.index("notice:tool_call")
    tool_result_notice = timeline.index("notice:tool_result")
    assert (
        timeline.index("commit_transition", timeline.index("model_generate") + 1)
        < tool_call_notice
    )
    assert timeline.index("commit_settlement") < tool_result_notice


@pytest.mark.asyncio
async def test_committed_fact_notice_delivery_is_ordered_after_commits() -> None:
    timeline: list[str] = []
    coordinator = _coordinator(
        SequenceModelAdapter([_model_result("done", content="done")], timeline),
        RecordingCommitPort(timeline),
        RecordingEffectExecutor(timeline),
    )

    await coordinator.run(
        _initial_request(),
        control_probe=QuietControlProbe(timeline),
        frame_sink=RecordingFrameSink(timeline),
        committed_fact_sink=RecordingFactSink(timeline),
    )

    assert timeline.index(
        "commit_transition", timeline.index("model_generate") + 1
    ) < timeline.index("notice:assistant_message")


@pytest.mark.asyncio
async def test_committed_fact_notice_sink_failure_disables_notices_and_reports_host_delivery_error_without_mutating_committed_facts() -> (
    None
):
    class FailingFactSink:
        def __init__(self) -> None:
            self.calls = 0

        async def emit(self, notice: CommittedFactNotice) -> None:
            del notice
            self.calls += 1
            raise RuntimeError()

    sink = FailingFactSink()
    commits = RecordingCommitPort()
    coordinator = _coordinator(
        SequenceModelAdapter([_model_result("done", content="done")]),
        commits,
        RecordingEffectExecutor(),
    )

    outcome = await coordinator.run(
        _initial_request(),
        control_probe=QuietControlProbe(),
        frame_sink=RecordingFrameSink(),
        committed_fact_sink=sink,
    )

    assert isinstance(outcome, CompletedOutcome)
    assert sink.calls == 1
    assert coordinator.delivery_failures[0].sink == "committed_fact"
    assert (
        commits.transition_requests[-1].proposal.pending_facts[0].fact_kind
        == "assistant_message"
    )


@pytest.mark.asyncio
async def test_committed_fact_notice_backpressure_is_independent_of_frame_sink() -> (
    None
):
    frame_release = asyncio.Event()
    frame_started = asyncio.Event()
    fact_release = asyncio.Event()
    fact_started = asyncio.Event()

    class FrameSink:
        async def emit(self, frame: StreamFrame) -> None:
            del frame
            frame_started.set()
            await frame_release.wait()

    class FactSink:
        async def emit(self, notice: CommittedFactNotice) -> None:
            del notice
            fact_started.set()
            await fact_release.wait()

    frame = StreamFrame(
        frame_id="frame-1",
        kind=StreamFrameKind.TOKEN_DELTA,
        payload={"text": "partial"},
    )
    coordinator = _coordinator(
        SequenceModelAdapter([_model_result("done", content="done")], frame=frame),
        RecordingCommitPort(),
        RecordingEffectExecutor(),
    )
    task = asyncio.create_task(
        coordinator.run(
            _initial_request(),
            control_probe=QuietControlProbe(),
            frame_sink=FrameSink(),
            committed_fact_sink=FactSink(),
        )
    )

    await frame_started.wait()
    assert not fact_started.is_set()
    frame_release.set()
    await fact_started.wait()
    assert task.done() is False
    fact_release.set()
    assert isinstance(await task, CompletedOutcome)


@pytest.mark.asyncio
async def test_frame_sink_and_fact_sink_delivery_failure_does_not_roll_back_durable_work() -> (
    None
):
    class FailingSink:
        async def emit(self, item: Any) -> None:
            del item
            raise RuntimeError("delivery unavailable")

    commits = RecordingCommitPort()
    frame = StreamFrame(
        frame_id="frame-1",
        kind=StreamFrameKind.TOKEN_DELTA,
        payload={"text": "partial"},
    )
    coordinator = _coordinator(
        SequenceModelAdapter([_model_result("done", content="done")], frame=frame),
        commits,
        RecordingEffectExecutor(),
    )

    outcome = await coordinator.run(
        _initial_request(),
        control_probe=QuietControlProbe(),
        frame_sink=FailingSink(),
        committed_fact_sink=FailingSink(),
    )

    assert isinstance(outcome, CompletedOutcome)
    assert len(commits.transition_requests) == 2
    assert {failure.sink for failure in coordinator.delivery_failures} == {
        "frame",
        "committed_fact",
    }


@pytest.mark.asyncio
async def test_completed_effect_settlement_commits_before_probe_safe_yield() -> None:
    timeline: list[str] = []

    class RaiseAfterExecutionProbe(QuietControlProbe):
        def observe(self) -> ControlSnapshot:
            self.timeline.append("control_observe")
            self.observe_count += 1
            executed = "effect_execute" in self.timeline
            return ControlSnapshot(
                generation=ControlGeneration(self.observe_count),
                raised=executed,
                reason="interrupt" if executed else None,
            )

    commits = RecordingCommitPort(timeline)
    coordinator = _coordinator(
        SequenceModelAdapter(
            [_model_result("result-tool", content="", tool_calls=(_tool_call(),))],
            timeline,
        ),
        commits,
        RecordingEffectExecutor(timeline),
    )

    outcome = await coordinator.run(
        _initial_request(),
        control_probe=RaiseAfterExecutionProbe(timeline),
        frame_sink=RecordingFrameSink(timeline),
        committed_fact_sink=RecordingFactSink(timeline),
    )

    assert isinstance(outcome, SafeYieldOutcome)
    assert len(commits.settlement_requests) == 1
    assert timeline.index("effect_execute") < timeline.index("commit_settlement")


@pytest.mark.asyncio
async def test_executor_exception_commits_indeterminate_settlement() -> None:
    timeline: list[str] = []

    class RaisingExecutor(RecordingEffectExecutor):
        async def execute(self, permit, cancellation):
            self.timeline.append("effect_execute")
            self.calls.append((permit, cancellation))
            raise RuntimeError()

    commits = RecordingCommitPort(timeline)
    coordinator = _coordinator(
        SequenceModelAdapter(
            [_model_result("result-tool", content="", tool_calls=(_tool_call(),))],
            timeline,
        ),
        commits,
        RaisingExecutor(timeline),
    )

    outcome = await coordinator.run(
        _initial_request(),
        control_probe=QuietControlProbe(timeline),
        frame_sink=RecordingFrameSink(timeline),
        committed_fact_sink=RecordingFactSink(timeline),
    )

    assert isinstance(outcome, BlockedOutcome)
    assert len(commits.settlement_requests) == 1
    settlement = commits.settlement_requests[0].settlement
    assert settlement.outcome is EffectSettlementOutcome.INDETERMINATE
    assert settlement.reason_code == "effect_executor_error"
    assert settlement.reason_message == "RuntimeError"


@pytest.mark.asyncio
async def test_executor_task_cancellation_commits_indeterminate_before_propagating() -> (
    None
):
    class CancelledExecutor(RecordingEffectExecutor):
        async def execute(self, permit, cancellation):
            self.calls.append((permit, cancellation))
            raise asyncio.CancelledError

    commits = RecordingCommitPort()
    coordinator = _coordinator(
        SequenceModelAdapter(
            [_model_result("result-tool", content="", tool_calls=(_tool_call(),))]
        ),
        commits,
        CancelledExecutor(),
    )

    with pytest.raises(asyncio.CancelledError):
        await coordinator.run(
            _initial_request(),
            control_probe=QuietControlProbe(),
            frame_sink=RecordingFrameSink(),
            committed_fact_sink=RecordingFactSink(),
        )

    assert len(commits.settlement_requests) == 1
    settlement = commits.settlement_requests[0].settlement
    assert settlement.outcome is EffectSettlementOutcome.INDETERMINATE
    assert settlement.reason_code == "effect_executor_cancelled"


@pytest.mark.asyncio
async def test_failed_effect_message_reaches_committed_tool_result_notice() -> None:
    class FailedExecutor(RecordingEffectExecutor):
        async def execute(self, permit, cancellation):
            self.calls.append((permit, cancellation))
            return EffectFailedResult(
                error=FailureReport(
                    code="tool_failed",
                    message="tool execution failed",
                )
            )

    commits = RecordingCommitPort()
    facts = RecordingFactSink()
    coordinator = _coordinator(
        SequenceModelAdapter(
            [
                _model_result("result-tool", content="", tool_calls=(_tool_call(),)),
                _model_result("result-done", content="continued"),
            ]
        ),
        commits,
        FailedExecutor(),
    )

    outcome = await coordinator.run(
        _initial_request(),
        control_probe=QuietControlProbe(),
        frame_sink=RecordingFrameSink(),
        committed_fact_sink=facts,
    )

    assert isinstance(outcome, CompletedOutcome)
    settlement = commits.settlement_requests[0].settlement
    assert settlement.result == "tool execution failed"
    tool_result = next(
        notice for notice in facts.notices if notice.fact_kind == "tool_result"
    )
    assert tool_result.payload["result"] == "tool execution failed"
    assert tool_result.payload["is_error"] is True
