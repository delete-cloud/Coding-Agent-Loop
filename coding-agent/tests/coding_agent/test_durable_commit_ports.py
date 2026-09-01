from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from agentkit.runtime import (
    CommitRef,
    ApprovalResolved,
    ApprovalSettlement,
    AgentEngine,
    CommitTransitionRequest,
    CommitSettlementRequest,
    CompletedOutcome,
    EffectSettled,
    FailedOutcome,
    EffectSettlement,
    EffectSettlementOutcome,
    FailureReport,
    CommittedCommitResult,
    DispatchAuthorizationRequest,
    DispatchAuthorizedResult,
    EffectMutation,
    EffectPlan,
    EffectStatus,
    EngineStepRequest,
    ExactReplayCommitResult,
    Initial,
    OperationStateVersion,
    ModelGenerationAction,
    PendingFact,
    PreparedEffectAction,
    PreparedEffectActionKind,
    RuntimeCommand,
    RunSegmentRequest,
    StaleMailboxCutCommitResult,
    StorageFailureCommitResult,
    TransitionProposal,
)
from coding_agent.runs.turn_execution import DurableSegmentRunner
from coding_agent.stores.durable_commit_port import (
    AuthorizationReplayMarker,
    AuthorizationReplayRecovery,
    PostgreSQLCommitPort,
    SQLiteCommitPort,
)
from coding_agent.server.stores.session_owner_store import OwnerAuthority
from coding_agent.stores.durable_local import SQLiteLocalDurableStore
from coding_agent.stores.durable_pg import PGDurableStore
from tests.coding_agent.test_harness_p2_fact_source import HarnessFakePGPool
from coding_agent.runs.child_execution import approval_resolved_input_id

SESSION_ID = "session-commit-port"
OWNER_ID = "owner-commit-port"
SESSION_STATE = {
    "id": SESSION_ID,
    "session_id": SESSION_ID,
    "tape_id": None,
    "status": "active",
}
STAMP = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
PORT_CASES = (
    ("sqlite", SQLiteCommitPort),
    ("pg", PostgreSQLCommitPort),
)


def _state(*, revision: int = 0, value: dict[str, object] | None = None):
    return OperationStateVersion(
        run_id="run-commit-port",
        revision=revision,
        projection_epoch=0,
        commit_ref=CommitRef(transition_id="admission"),
        value={} if value is None else value,
    )


def _plan(effect_id: str) -> EffectPlan:
    return EffectPlan(
        effect_id=effect_id,
        attempt_id=f"attempt-{effect_id}",
        effect_kind="tool",
        payload={
            "tool_call_id": f"call-{effect_id}",
            "tool_name": "read",
            "arguments": {"path": effect_id},
        },
        idempotency_key=f"idempotency-{effect_id}",
    )


def _subagent_plan(effect_id: str) -> EffectPlan:
    plan = _plan(effect_id)
    return EffectPlan(
        effect_id=plan.effect_id,
        attempt_id=plan.attempt_id,
        effect_kind=plan.effect_kind,
        payload={
            **plan.payload,
            "tool_name": "subagent",
            "arguments": {"task": "inspect"},
        },
        idempotency_key=plan.idempotency_key,
    )


def _pending_plan_value(plan: EffectPlan) -> dict[str, object]:
    return {
        "_agentkit_runtime": {
            "pending_effect_plans": [
                {
                    "effect_id": plan.effect_id,
                    "attempt_id": plan.attempt_id,
                    "effect_kind": plan.effect_kind,
                    "payload": dict(plan.payload),
                    "idempotency_key": plan.idempotency_key,
                    "requires_approval": plan.requires_approval,
                    "approval_request_id": plan.approval_request_id,
                }
            ]
        }
    }


def _prepare_request(*plans: EffectPlan) -> CommitTransitionRequest:
    state = _state()
    proposal = TransitionProposal(
        transition_id="prepare-effects",
        state_value=_pending_plan_value(plans[0]),
        next_action=PreparedEffectAction(
            effect_plan=plans[0],
            action_kind=PreparedEffectActionKind.DISPATCH,
        ),
        pending_facts=(
            PendingFact(
                fact_id="fact-prepared",
                fact_kind="effect_prepared",
                payload={"count": len(plans)},
            ),
        ),
        effect_plans=plans,
    )
    return CommitTransitionRequest(
        session_id=SESSION_ID,
        owner_id=OWNER_ID,
        owner_epoch=1,
        engine_request=EngineStepRequest(
            state_version=state,
            step_input=Initial(
                input_id="initial-prepare",
                command_batch=(),
                mailbox_cut=0,
            ),
        ),
        proposal=proposal,
    )


async def _open_store(tmp_path: Path, store_kind: str):
    if store_kind == "sqlite":
        store = SQLiteLocalDurableStore(tmp_path / "commit-port.sqlite3")
        owner = await store.acquire_owner(SESSION_ID, OWNER_ID)
        await store.save_session(owner, SESSION_STATE)
        return store, owner
    pool = HarnessFakePGPool()
    owner = OwnerAuthority(SESSION_ID, OWNER_ID, 1)
    pool.seed_owner(owner)
    store = PGDurableStore(pool=cast(Any, pool))
    store._harness_pool = pool  # type: ignore[attr-defined]
    await store.save_session(owner, SESSION_STATE)
    return store, owner


@pytest.mark.asyncio
@pytest.mark.parametrize(("store_kind", "port_type"), PORT_CASES)
async def test_concrete_commit_ports_prepare_all_plans_and_restore_exact_receipt(
    tmp_path: Path,
    store_kind: str,
    port_type,
) -> None:
    store, _ = await _open_store(tmp_path, store_kind)
    plans = (_plan("effect-one"), _plan("effect-two"))
    port = port_type(store, session_state=SESSION_STATE, clock=lambda: STAMP)
    request = _prepare_request(*plans)

    committed = await port.commit_transition(request)

    assert isinstance(committed, CommittedCommitResult)
    assert committed.receipt is not None
    assert committed.receipt.effect_plans == plans
    assert committed.notices[0].event_record_id == "fact-prepared"
    assert (await store.load_effect_slot(SESSION_ID, "effect-one")).status == "prepared"
    assert (await store.load_effect_slot(SESSION_ID, "effect-two")).status == "prepared"

    replay = await port.commit_transition(request)
    assert isinstance(replay, ExactReplayCommitResult)
    assert replay.receipt.effect_plans == plans


@pytest.mark.asyncio
@pytest.mark.parametrize(("store_kind", "port_type"), PORT_CASES)
async def test_child_identity_uses_full_durable_tuple_and_frozen_settlement_formula(
    tmp_path: Path,
    store_kind: str,
    port_type,
) -> None:
    store, _ = await _open_store(tmp_path, store_kind)
    plan = _subagent_plan("parent-subagent")
    port = port_type(store, session_state=SESSION_STATE, clock=lambda: STAMP)

    committed = await port.commit_transition(_prepare_request(plan))

    assert isinstance(committed, CommittedCommitResult)
    binding = await store.load_child_execution_binding(
        SESSION_ID,
        parent_effect_id=plan.effect_id,
    )
    assert binding is not None
    assert binding.parent_run_id == "run-commit-port"
    assert binding.child_run_id == (
        f"{SESSION_ID}:run-commit-port:child:{plan.effect_id}:{plan.attempt_id}"
    )
    assert binding.authorization_transition_id == (
        f"prepare-effects:dispatch:{plan.effect_id}:{plan.attempt_id}"
    )
    assert binding.parent_effect_id == plan.effect_id
    assert binding.parent_attempt_id == plan.attempt_id
    settlement_input_id = (
        f"{binding.authorization_transition_id}:settlement:{plan.attempt_id}"
    )
    assert binding.live_parent_settlement_transition_id == (
        f"run-commit-port:transition:EffectSettled:{settlement_input_id}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("store_kind", "port_type"), PORT_CASES)
async def test_authorization_replay_never_mints_a_second_live_permit(
    tmp_path: Path,
    store_kind: str,
    port_type,
) -> None:
    store, _ = await _open_store(tmp_path, store_kind)
    plan = _plan("effect-authorize")
    port = port_type(
        store,
        session_state=SESSION_STATE,
        clock=lambda: STAMP,
        permit_token_factory=lambda: "opaque-permit",
    )
    prepared = await port.commit_transition(_prepare_request(plan))
    assert isinstance(prepared, CommittedCommitResult)
    proposal = TransitionProposal(
        transition_id="authorize-effect",
        state_value=prepared.state_version.value,
        next_action=PreparedEffectAction(
            effect_plan=plan,
            action_kind=PreparedEffectActionKind.DISPATCH,
        ),
    )
    request = DispatchAuthorizationRequest(
        session_id=SESSION_ID,
        owner_id=OWNER_ID,
        owner_epoch=1,
        mailbox_cut=0,
        engine_request=EngineStepRequest(
            state_version=prepared.state_version,
            step_input=Initial(
                input_id="initial-authorize",
                command_batch=(),
                mailbox_cut=0,
            ),
        ),
        proposal=proposal,
        effect_plan=plan,
        effect_mutation=EffectMutation(
            effect_id=plan.effect_id,
            attempt_id=plan.attempt_id,
            expected_status=EffectStatus.PREPARED,
            status=EffectStatus.DISPATCHED,
            payload={"authorization_transition_id": proposal.transition_id},
        ),
    )

    authorized = await port.authorize_dispatch(request)
    assert isinstance(authorized, DispatchAuthorizedResult)
    assert authorized.permit.claimed is False
    replay = await port.authorize_dispatch(request)
    assert isinstance(replay, ExactReplayCommitResult)


@pytest.mark.asyncio
@pytest.mark.parametrize(("store_kind", "port_type"), PORT_CASES)
async def test_authorization_maps_stale_mailbox_cut_without_permit(
    tmp_path: Path,
    store_kind: str,
    port_type,
) -> None:
    store, owner = await _open_store(tmp_path, store_kind)
    plan = _plan("effect-stale")
    port = port_type(store, session_state=SESSION_STATE, clock=lambda: STAMP)
    prepared = await port.commit_transition(_prepare_request(plan))
    assert isinstance(prepared, CommittedCommitResult)
    await store.admit_runtime_command(
        owner,
        RuntimeCommand(
            command_id="command-sibling",
            command_kind="cancel",
            payload={"target_run_id": "sibling-run"},
        ),
    )
    proposal = TransitionProposal(
        transition_id="authorize-stale",
        state_value=prepared.state_version.value,
        next_action=PreparedEffectAction(
            effect_plan=plan,
            action_kind=PreparedEffectActionKind.DISPATCH,
        ),
    )
    request = DispatchAuthorizationRequest(
        session_id=SESSION_ID,
        owner_id=OWNER_ID,
        owner_epoch=1,
        mailbox_cut=0,
        engine_request=EngineStepRequest(
            state_version=prepared.state_version,
            step_input=Initial(
                input_id="initial-stale",
                command_batch=(),
                mailbox_cut=0,
            ),
        ),
        proposal=proposal,
        effect_plan=plan,
        effect_mutation=EffectMutation(
            effect_id=plan.effect_id,
            attempt_id=plan.attempt_id,
            expected_status=EffectStatus.PREPARED,
            status=EffectStatus.DISPATCHED,
            payload={"authorization_transition_id": proposal.transition_id},
        ),
    )

    result = await port.authorize_dispatch(request)

    assert result == StaleMailboxCutCommitResult(
        expected_mailbox_cut=0,
        current_mailbox_cut=1,
    )


@pytest.mark.asyncio
async def test_commit_port_storage_error_message_is_never_blank() -> None:
    class FailingStore:
        async def commit_authoritative_uow(self, authority, unit):
            del authority, unit
            raise RuntimeError()

    result = await SQLiteCommitPort(
        FailingStore(),
        session_state=SESSION_STATE,
        clock=lambda: STAMP,
    ).commit_transition(_prepare_request(_plan("effect-failure")))

    assert isinstance(result, StorageFailureCommitResult)
    assert result.error.message.strip()


class _ReplayCoordinator:
    def __init__(self, terminal_message: str) -> None:
        self.requests: list[RunSegmentRequest] = []
        self.terminal_message = terminal_message

    async def run(self, request, control_probe, frame_sink, committed_fact_sink):
        del control_probe, frame_sink, committed_fact_sink
        self.requests.append(request)
        if len(self.requests) == 1:
            return FailedOutcome(
                state_version=request.state_version,
                error=FailureReport(
                    code="exact_replay_requires_recovery",
                    message="authorization replay requires recovery",
                ),
                steps_taken=0,
            )
        assert isinstance(request.step_input, EffectSettled)
        return CompletedOutcome(
            state_version=request.state_version,
            final_message=self.terminal_message,
            steps_taken=0,
            stop_reason="replayed",
        )


class _ReplayPort:
    def __init__(
        self,
        marker: AuthorizationReplayMarker,
        recovery: AuthorizationReplayRecovery,
    ) -> None:
        self.marker = marker
        self.recovery = recovery
        self.consumed = 0

    def consume_authorization_replay_marker(self, request):
        del request
        if self.consumed:
            return None
        self.consumed += 1
        return self.marker

    async def recover_authorization_without_marker(self, request):
        del request
        return None

    async def recover_authorization_replay(self, marker):
        assert marker == self.marker
        return self.recovery


class _UnusedSink:
    async def emit(self, value) -> None:
        del value

    async def emit_all(self, values) -> None:
        del values


class _UnusedProbe:
    def observe(self):
        raise AssertionError("fake replay coordinator must not observe controls")


async def _run_replay_scenario(
    *,
    run_id: str,
    terminal_message: str,
) -> tuple[CompletedOutcome, _ReplayCoordinator, _ReplayPort]:
    initial_state = OperationStateVersion(
        run_id=run_id,
        revision=0,
        projection_epoch=0,
        commit_ref=CommitRef(transition_id="prepared"),
        value={},
    )
    state = OperationStateVersion(
        run_id=run_id,
        revision=1,
        projection_epoch=0,
        commit_ref=CommitRef(transition_id="authorization"),
        value={
            "_agentkit_runtime": {
                "mailbox_cut": 0,
                "pending_effect_plans": [
                    {
                        "effect_id": "effect-replay",
                        "attempt_id": "attempt-replay",
                        "effect_kind": "tool",
                        "payload": {
                            "tool_call_id": "call-replay",
                            "tool_name": "read",
                            "arguments": {},
                        },
                        "idempotency_key": None,
                        "requires_approval": False,
                        "approval_request_id": None,
                    }
                ],
                "active_effect_authorization": {
                    "effect_id": "effect-replay",
                    "attempt_id": "attempt-replay",
                    "tool_call_id": "call-replay",
                    "tool_name": "read",
                    "authorization_transition_id": "authorize-effect",
                    "dispatch_owner_epoch": 1,
                },
            }
        },
    )
    marker = AuthorizationReplayMarker(
        session_id=SESSION_ID,
        run_id=run_id,
        authorization_transition_id="authorize-effect",
        effect_id="effect-replay",
        attempt_id="attempt-replay",
        owner_epoch=1,
        authorization_state=state,
    )
    settlement = EffectSettlement(
        input_id="authorize-effect:settlement:attempt-replay",
        tool_call_id="call-replay",
        tool_name="read",
        effect_id="effect-replay",
        attempt_id="attempt-replay",
        authorization_transition_id="authorize-effect",
        owner_epoch=1,
        outcome=EffectSettlementOutcome.INDETERMINATE,
        result={},
        reason_code="authorization_replay_unclaimed",
        reason_message="authorized effect execution could not be proven",
    )
    port = _ReplayPort(
        marker,
        AuthorizationReplayRecovery(
            state_version=state,
            step_input=EffectSettled(settlement=settlement),
        ),
    )
    coordinator = _ReplayCoordinator(terminal_message)
    outcome = await DurableSegmentRunner(
        coordinator=coordinator,
        commit_port=port,
    ).run(
        RunSegmentRequest(
            session_id=SESSION_ID,
            owner_id=OWNER_ID,
            owner_epoch=1,
            state_version=initial_state,
            step_input=Initial(
                input_id=f"{run_id}:initial",
                command_batch=(),
                mailbox_cut=0,
            ),
            max_rounds=3,
        ),
        _UnusedProbe(),
        _UnusedSink(),
        _UnusedSink(),
    )
    assert isinstance(outcome, CompletedOutcome)
    return outcome, coordinator, port


@pytest.mark.asyncio
async def test_replay_runner_reenters_with_effect_settled_and_returns_eventual_root_outcome() -> (
    None
):
    outcome, coordinator, port = await _run_replay_scenario(
        run_id="root-run",
        terminal_message="root complete",
    )

    assert outcome.final_message == "root complete"
    assert len(coordinator.requests) == 2
    assert isinstance(coordinator.requests[1].step_input, EffectSettled)
    assert port.consumed == 1


@pytest.mark.asyncio
async def test_replay_runner_reenters_with_effect_settled_and_returns_eventual_child_outcome() -> (
    None
):
    outcome, coordinator, port = await _run_replay_scenario(
        run_id="child-run",
        terminal_message="child complete",
    )
    assert outcome.final_message == "child complete"
    assert coordinator.requests[1].state_version.run_id == "child-run"
    assert port.consumed == 1


async def _authorized_replay_port(
    tmp_path: Path,
    store_kind: str,
):
    store, _owner = await _open_store(tmp_path, store_kind)
    plan = _plan("effect-durable-replay")
    port_type = SQLiteCommitPort if store_kind == "sqlite" else PostgreSQLCommitPort
    port = port_type(
        store,
        session_state=SESSION_STATE,
        clock=lambda: STAMP,
        permit_token_factory=lambda: "durable-replay-permit",
    )
    prepared = await port.commit_transition(_prepare_request(plan))
    assert isinstance(prepared, CommittedCommitResult)
    authorization_id = "authorize-durable-replay"
    active_value = {
        **prepared.state_version.value,
        "_agentkit_runtime": {
            **prepared.state_version.value.get("_agentkit_runtime", {}),
            "active_effect_authorization": {
                "effect_id": plan.effect_id,
                "attempt_id": plan.attempt_id,
                "tool_call_id": plan.payload["tool_call_id"],
                "tool_name": plan.payload["tool_name"],
                "authorization_transition_id": authorization_id,
                "dispatch_owner_epoch": 1,
            },
        },
    }
    request = DispatchAuthorizationRequest(
        session_id=SESSION_ID,
        owner_id=OWNER_ID,
        owner_epoch=1,
        mailbox_cut=0,
        engine_request=EngineStepRequest(
            state_version=prepared.state_version,
            step_input=Initial(
                input_id="durable-replay-input",
                command_batch=(),
                mailbox_cut=0,
            ),
        ),
        proposal=TransitionProposal(
            transition_id=authorization_id,
            state_value=active_value,
            next_action=PreparedEffectAction(
                effect_plan=plan,
                action_kind=PreparedEffectActionKind.DISPATCH,
            ),
        ),
        effect_plan=plan,
        effect_mutation=EffectMutation(
            effect_id=plan.effect_id,
            attempt_id=plan.attempt_id,
            expected_status=EffectStatus.PREPARED,
            status=EffectStatus.DISPATCHED,
            payload={},
        ),
    )
    authorized = await port.authorize_dispatch(request)
    assert isinstance(authorized, DispatchAuthorizedResult)
    replay = await port.authorize_dispatch(request)
    assert isinstance(replay, ExactReplayCommitResult)
    runner_request = RunSegmentRequest(
        session_id=SESSION_ID,
        owner_id=OWNER_ID,
        owner_epoch=1,
        state_version=authorized.state_version,
        step_input=EffectSettled(
            settlement=EffectSettlement(
                input_id="post-crash:settlement",
                tool_call_id=str(plan.payload["tool_call_id"]),
                tool_name=str(plan.payload["tool_name"]),
                effect_id=plan.effect_id,
                attempt_id=plan.attempt_id,
                authorization_transition_id=authorization_id,
                owner_epoch=1,
                outcome=EffectSettlementOutcome.INDETERMINATE,
                result={},
                reason_code="post_crash_probe",
                reason_message="post-crash recovery probe",
            )
        ),
        max_rounds=3,
    )
    return store, port_type, port, runner_request


async def _commit_indeterminate_closeout(
    port: Any,
    state: OperationStateVersion,
    settlement: EffectSettlement,
):
    engine_request = EngineStepRequest(
        state_version=state,
        step_input=EffectSettled(settlement=settlement),
    )
    proposal = AgentEngine().propose(engine_request)
    return await port.commit_settlement(
        CommitSettlementRequest(
            session_id=SESSION_ID,
            owner_id=OWNER_ID,
            owner_epoch=1,
            engine_request=engine_request,
            proposal=proposal,
            settlement=settlement,
            effect_mutation=EffectMutation(
                effect_id=settlement.effect_id,
                attempt_id=settlement.attempt_id,
                expected_status=EffectStatus.DISPATCHED,
                status=EffectStatus.UNKNOWN,
                payload={
                    "authorization_transition_id": (
                        settlement.authorization_transition_id
                    ),
                    "dispatch_owner_epoch": settlement.owner_epoch,
                    "result": settlement.result,
                    "reason_code": settlement.reason_code,
                    "reason_message": settlement.reason_message,
                },
            ),
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_authorization_lost_ack_quiesces_and_settles_indeterminate(
    tmp_path: Path,
    store_kind: str,
) -> None:
    store, port_type, _port, runner_request = await _authorized_replay_port(
        tmp_path,
        store_kind,
    )
    restarted = port_type(
        store,
        session_state=SESSION_STATE,
        clock=lambda: STAMP,
    )
    recovery = await restarted.recover_authorization_without_marker(runner_request)
    assert recovery is not None

    committed = await _commit_indeterminate_closeout(
        restarted,
        recovery.state_version,
        recovery.step_input.settlement,
    )
    attempt = await store.load_executor_attempt(
        SESSION_ID,
        "effect-durable-replay",
        "attempt-effect-durable-replay",
        "authorize-durable-replay",
    )
    effect = await store.load_effect_slot(SESSION_ID, "effect-durable-replay")

    assert isinstance(committed, CommittedCommitResult)
    assert attempt is not None and attempt.status == "quiescent"
    assert effect is not None and effect.status == "unknown"
    assert effect.payload["reason_code"] == "authorization_replay_unclaimed"


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_post_authorization_control_quiesces_unstarted_attempt_and_settles_unknown(
    tmp_path: Path,
    store_kind: str,
) -> None:
    store, port_type, _port, runner_request = await _authorized_replay_port(
        tmp_path,
        store_kind,
    )
    plan = _plan("effect-durable-replay")
    settlement = EffectSettlement(
        input_id="post-authorization-control",
        tool_call_id=str(plan.payload["tool_call_id"]),
        tool_name=str(plan.payload["tool_name"]),
        effect_id=plan.effect_id,
        attempt_id=plan.attempt_id,
        authorization_transition_id="authorize-durable-replay",
        owner_epoch=1,
        outcome=EffectSettlementOutcome.INDETERMINATE,
        result={},
        reason_code="post_authorization_control",
        reason_message="control arrived before executor claim",
    )
    port = port_type(
        store,
        session_state=SESSION_STATE,
        clock=lambda: STAMP,
    )

    committed = await _commit_indeterminate_closeout(
        port,
        runner_request.state_version,
        settlement,
    )
    attempt = await store.load_executor_attempt(
        SESSION_ID,
        plan.effect_id,
        plan.attempt_id,
        "authorize-durable-replay",
    )
    effect = await store.load_effect_slot(SESSION_ID, plan.effect_id)

    assert isinstance(committed, CommittedCommitResult)
    assert attempt is not None and attempt.status == "quiescent"
    assert attempt.quiescence_evidence_ref is not None
    assert effect is not None and effect.status == "unknown"
    assert effect.payload["reason_code"] == "post_authorization_control"


@pytest.mark.asyncio
@pytest.mark.parametrize(("store_kind", "port_type"), PORT_CASES)
async def test_precommitted_denial_reentry_exact_replays_then_continues_once(
    tmp_path: Path,
    store_kind: str,
    port_type,
) -> None:
    store, owner = await _open_store(tmp_path, store_kind)
    base_plan = _plan("effect-denied")
    denial_input_id = approval_resolved_input_id("run-commit-port", "deny-command")
    plan = EffectPlan(
        effect_id=base_plan.effect_id,
        attempt_id=base_plan.attempt_id,
        effect_kind=base_plan.effect_kind,
        payload=base_plan.payload,
        idempotency_key=base_plan.idempotency_key,
        requires_approval=True,
        approval_request_id=denial_input_id,
    )
    port = port_type(store, session_state=SESSION_STATE, clock=lambda: STAMP)
    prepared = await port.commit_transition(_prepare_request(plan))
    assert isinstance(prepared, CommittedCommitResult)
    await store.admit_new_runtime_command(
        owner,
        RuntimeCommand(
            command_id="deny-command",
            command_kind="approval_decision",
            payload={
                "approved": False,
                "request_id": denial_input_id,
                "target_run_id": prepared.state_version.run_id,
            },
        ),
    )
    denial = ApprovalSettlement(
        input_id=denial_input_id,
        command_id="deny-command",
        tool_call_id=str(plan.payload["tool_call_id"]),
        tool_name=str(plan.payload["tool_name"]),
        effect_id=plan.effect_id,
        attempt_id=plan.attempt_id,
        transition_id="prepare-effects",
        owner_epoch=1,
        approved=False,
        rejection_reason_code="user_denied",
        rejection_message="user denied the effect",
    )
    assert ApprovalResolved(settlement=denial).input_id == denial_input_id
    engine_request = EngineStepRequest(
        state_version=prepared.state_version,
        step_input=ApprovalResolved(settlement=denial),
    )
    proposal = AgentEngine().propose(engine_request)
    request = CommitSettlementRequest(
        session_id=SESSION_ID,
        owner_id=OWNER_ID,
        owner_epoch=1,
        engine_request=engine_request,
        proposal=proposal,
        settlement=denial,
        effect_mutation=EffectMutation(
            effect_id=plan.effect_id,
            attempt_id=plan.attempt_id,
            expected_status=EffectStatus.PREPARED,
            status=EffectStatus.REJECTED,
            payload={
                "reason_code": "user_denied",
                "reason_message": "user denied the effect",
            },
        ),
    )

    committed = await port.commit_settlement(request)
    replay = await port.commit_settlement(request)
    facts = await store.replay_from_retention_floor(SESSION_ID)
    effect = await store.load_effect_slot(SESSION_ID, plan.effect_id)

    assert isinstance(committed, CommittedCommitResult)
    assert isinstance(replay, ExactReplayCommitResult)
    assert replay.state_version == committed.state_version
    assert isinstance(proposal.next_action, ModelGenerationAction)
    assert [fact.event_kind for fact in facts.events].count("tool_result") == 1
    assert effect is not None and effect.status == "rejected"
    assert (
        await store.load_executor_attempt(
            SESSION_ID,
            plan.effect_id,
            plan.attempt_id,
            "prepare-effects",
        )
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_authorization_replay_marker_is_process_local_and_consumed_by_matching_runner(
    tmp_path: Path,
    store_kind: str,
) -> None:
    store, port_type, port, runner_request = await _authorized_replay_port(
        tmp_path,
        store_kind,
    )

    marker = port.consume_authorization_replay_marker(runner_request)

    assert marker is not None
    assert marker.run_id == runner_request.state_version.run_id
    assert port.consume_authorization_replay_marker(runner_request) is None
    restarted = port_type(store, session_state=SESSION_STATE)
    assert restarted.consume_authorization_replay_marker(runner_request) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_recovered_child_reconstructs_authorization_crash_from_durable_attempt_without_marker(
    tmp_path: Path,
    store_kind: str,
) -> None:
    store, port_type, _port, runner_request = await _authorized_replay_port(
        tmp_path,
        store_kind,
    )
    restarted = port_type(store, session_state=SESSION_STATE)

    recovery = await restarted.recover_authorization_without_marker(runner_request)

    assert recovery is not None
    assert isinstance(recovery.step_input, EffectSettled)
    assert (
        recovery.step_input.settlement.outcome is EffectSettlementOutcome.INDETERMINATE
    )
    assert (
        recovery.step_input.settlement.reason_code == "authorization_replay_unclaimed"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_authorization_recovery_waits_for_claimed_attempt_quiescence(
    tmp_path: Path,
    store_kind: str,
) -> None:
    store, port_type, _port, runner_request = await _authorized_replay_port(
        tmp_path,
        store_kind,
    )
    authority = OwnerAuthority(SESSION_ID, OWNER_ID, 1)
    await store.reserve_executor_attempt(
        authority,
        effect_id="effect-durable-replay",
        attempt_id="attempt-effect-durable-replay",
        authorization_transition_id="authorize-durable-replay",
        executor_id="active-executor",
        lease_expires_at=STAMP + timedelta(seconds=30),
    )
    restarted = port_type(store, session_state=SESSION_STATE)
    recovery_task = asyncio.create_task(
        restarted.recover_authorization_without_marker(runner_request)
    )
    await asyncio.sleep(0.02)
    assert recovery_task.done() is False

    await store.quiesce_claimed_executor_attempt(
        authority,
        effect_id="effect-durable-replay",
        attempt_id="attempt-effect-durable-replay",
        authorization_transition_id="authorize-durable-replay",
        executor_id="active-executor",
        now=STAMP,
        evidence_ref="active-executor-quiesced",
    )
    recovery = await asyncio.wait_for(recovery_task, timeout=1)

    assert recovery is not None
    assert (
        recovery.step_input.settlement.outcome is EffectSettlementOutcome.INDETERMINATE
    )


@pytest.mark.asyncio
async def test_root_and_child_authorization_replay_use_durable_segment_runner() -> None:
    root, root_coordinator, _root_port = await _run_replay_scenario(
        run_id="root-run",
        terminal_message="root done",
    )
    child, child_coordinator, _child_port = await _run_replay_scenario(
        run_id="child-run",
        terminal_message="child done",
    )

    assert root.final_message == "root done"
    assert child.final_message == "child done"
    assert isinstance(root_coordinator.requests[1].step_input, EffectSettled)
    assert isinstance(child_coordinator.requests[1].step_input, EffectSettled)
