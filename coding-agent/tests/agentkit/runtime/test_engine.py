from __future__ import annotations

import inspect
import pytest

from agentkit.runtime import (
    AgentEngine,
    ApprovalResolved,
    ApprovalSettlement,
    CommitRef,
    BlockedAction,
    EffectSettled,
    EffectSettlement,
    EffectSettlementOutcome,
    EngineStepRequest,
    Initial,
    ModelGenerationAction,
    ModelGenerationCompleted,
    ModelGenerationResult,
    ModelToolCall,
    ModelUsage,
    OperationStateVersion,
    PreparedEffectAction,
    ProviderStopMetadata,
    RuntimeCommand,
    TerminalAction,
)


def _state(
    *,
    revision: int = 0,
    value: dict[str, object] | None = None,
) -> OperationStateVersion:
    return OperationStateVersion(
        run_id="run-1",
        revision=revision,
        projection_epoch=2,
        commit_ref=CommitRef(transition_id=f"commit-{revision}"),
        value=value or {},
    )


def _committed(proposal, *, revision: int) -> OperationStateVersion:
    return _state(revision=revision, value=dict(proposal.state_value))


def _authorized_effect_state(
    proposal,
    *,
    revision: int,
    authorization_transition_id: str,
    owner_epoch: int,
) -> OperationStateVersion:
    plan = proposal.effect_plans[0]
    runtime = dict(proposal.state_value["_agentkit_runtime"])
    runtime["active_effect_authorization"] = {
        "effect_id": plan.effect_id,
        "attempt_id": plan.attempt_id,
        "tool_call_id": plan.payload["tool_call_id"],
        "tool_name": plan.payload["tool_name"],
        "authorization_transition_id": authorization_transition_id,
        "dispatch_owner_epoch": owner_epoch,
    }
    return _state(
        revision=revision,
        value={**proposal.state_value, "_agentkit_runtime": runtime},
    )


def _model_result(
    *tool_calls: ModelToolCall,
    content: str = "assistant answer",
    thinking: str | None = "final rationale",
) -> ModelGenerationResult:
    return ModelGenerationResult(
        result_id="model-result-1",
        request_id="run-1:model:1",
        assistant_content=content,
        finalized_thinking=thinking,
        tool_calls=tool_calls,
        usage=ModelUsage(input_tokens=11, output_tokens=7),
        provider_stop=ProviderStopMetadata(reason="tool_use" if tool_calls else "stop"),
    )


def _initial_proposal(engine: AgentEngine):
    return engine.propose(
        EngineStepRequest(
            state_version=_state(),
            step_input=Initial(
                input_id="initial-1",
                command_batch=(
                    RuntimeCommand(
                        command_id="command-1",
                        command_kind="user_prompt",
                        payload={"text": "inspect the repository"},
                    ),
                ),
                mailbox_cut=5,
            ),
        )
    )


def test_agent_engine_is_persistence_free_and_cannot_execute_effectful_tools() -> None:
    engine = AgentEngine()

    assert not hasattr(engine, "store")
    assert not hasattr(engine, "commit_port")
    assert not hasattr(engine, "effect_executor")
    assert not hasattr(engine, "execute")
    assert tuple(inspect.signature(engine.propose).parameters) == ("request",)


def test_engine_and_plugins_receive_no_store_executor_mailbox_cursor_or_dispatch_capability() -> (
    None
):
    signature = inspect.signature(AgentEngine.propose)
    forbidden = {"store", "executor", "mailbox", "cursor", "dispatch", "plugin"}

    assert forbidden.isdisjoint(signature.parameters)
    assert forbidden.isdisjoint(AgentEngine.__slots__)


def test_agent_engine_performs_no_adapter_io() -> None:
    engine = AgentEngine()
    proposal = _initial_proposal(engine)

    assert isinstance(proposal.next_action, ModelGenerationAction)
    assert not hasattr(engine, "model_adapter")
    assert not inspect.iscoroutinefunction(engine.propose)


def test_final_model_response_and_finalized_thinking_reenter_proposal_as_pending_facts() -> (
    None
):
    engine = AgentEngine()
    initial = _initial_proposal(engine)
    result = _model_result()

    proposal = engine.propose(
        EngineStepRequest(
            state_version=_committed(initial, revision=1),
            step_input=ModelGenerationCompleted(result=result),
        )
    )

    assert [
        (fact.fact_kind, dict(fact.payload)) for fact in proposal.pending_facts
    ] == [
        ("assistant_message", {"content": "assistant answer"}),
        ("finalized_thinking", {"text": "final rationale"}),
    ]
    assert isinstance(proposal.next_action, TerminalAction)
    assert proposal.next_action.final_message == "assistant answer"


def test_engine_step_reentry_model_only_commits_assistant_and_finalized_thinking_facts() -> (
    None
):
    engine = AgentEngine()
    initial = _initial_proposal(engine)

    proposal = engine.propose(
        EngineStepRequest(
            state_version=_committed(initial, revision=1),
            step_input=ModelGenerationCompleted(result=_model_result()),
        )
    )

    assert [fact.fact_kind for fact in proposal.pending_facts] == [
        "assistant_message",
        "finalized_thinking",
    ]
    assert proposal.effect_plans == ()


def test_engine_step_reentry_model_with_tool_produces_pending_facts_and_effect_plan() -> (
    None
):
    engine = AgentEngine()
    initial = _initial_proposal(engine)
    tool_call = ModelToolCall(
        tool_call_id="call-1",
        name="read",
        arguments={"path": "README.md"},
    )

    proposal = engine.propose(
        EngineStepRequest(
            state_version=_committed(initial, revision=1),
            step_input=ModelGenerationCompleted(result=_model_result(tool_call)),
        )
    )

    assert [fact.fact_kind for fact in proposal.pending_facts] == [
        "assistant_message",
        "finalized_thinking",
        "tool_call",
    ]
    assert len(proposal.effect_plans) == 1
    plan = proposal.effect_plans[0]
    assert plan.payload["tool_call_id"] == "call-1"
    assert plan.payload["tool_name"] == "read"
    assert plan.payload["arguments"] == {"path": "README.md"}
    assert isinstance(proposal.next_action, PreparedEffectAction)
    assert proposal.next_action.effect_plan == plan


def test_engine_step_reentry_preserves_multiple_tool_call_order_without_replay() -> (
    None
):
    engine = AgentEngine()
    initial = _initial_proposal(engine)
    calls = (
        ModelToolCall(
            tool_call_id="call-1",
            name="read",
            arguments={"path": "one"},
        ),
        ModelToolCall(
            tool_call_id="call-2",
            name="write",
            arguments={"path": "two", "content": "x"},
        ),
        ModelToolCall(
            tool_call_id="call-3",
            name="shell",
            arguments={"command": "pwd"},
        ),
    )

    proposal = engine.propose(
        EngineStepRequest(
            state_version=_committed(initial, revision=1),
            step_input=ModelGenerationCompleted(result=_model_result(*calls)),
        )
    )

    assert [plan.payload["tool_call_id"] for plan in proposal.effect_plans] == [
        "call-1",
        "call-2",
        "call-3",
    ]
    assert len({plan.effect_id for plan in proposal.effect_plans}) == 3
    assert [
        fact.payload["tool_call_id"]
        for fact in proposal.pending_facts
        if fact.fact_kind == "tool_call"
    ] == ["call-1", "call-2", "call-3"]


def test_engine_step_reentry_effect_settlement_commits_tool_result_fact_and_next_model_request() -> (
    None
):
    engine = AgentEngine()
    initial = _initial_proposal(engine)
    tool_call = ModelToolCall(
        tool_call_id="call-1",
        name="read",
        arguments={"path": "README.md"},
    )
    prepared = engine.propose(
        EngineStepRequest(
            state_version=_committed(initial, revision=1),
            step_input=ModelGenerationCompleted(result=_model_result(tool_call)),
        )
    )
    settlement = EffectSettlement.completed(
        input_id="settlement-1",
        tool_call_id="call-1",
        tool_name="read",
        effect_id=prepared.effect_plans[0].effect_id,
        attempt_id=prepared.effect_plans[0].attempt_id,
        authorization_transition_id="commit-2",
        owner_epoch=3,
        result={"content": "file contents"},
    )

    proposal = engine.propose(
        EngineStepRequest(
            state_version=_authorized_effect_state(
                prepared,
                revision=2,
                authorization_transition_id="commit-2",
                owner_epoch=3,
            ),
            step_input=EffectSettled(settlement=settlement),
        )
    )

    assert len(proposal.pending_facts) == 1
    assert proposal.pending_facts[0].fact_kind == "tool_result"
    assert proposal.pending_facts[0].payload["tool_call_id"] == "call-1"
    assert proposal.pending_facts[0].payload["result"] == {"content": "file contents"}
    assert isinstance(proposal.next_action, ModelGenerationAction)


def test_effect_settlement_rejects_missing_retained_authorization() -> None:
    engine = AgentEngine()
    initial = _initial_proposal(engine)
    prepared = engine.propose(
        EngineStepRequest(
            state_version=_committed(initial, revision=1),
            step_input=ModelGenerationCompleted(
                result=_model_result(
                    ModelToolCall(
                        tool_call_id="call-missing-authorization",
                        name="read",
                        arguments={"path": "README.md"},
                    )
                )
            ),
        )
    )
    plan = prepared.effect_plans[0]
    with pytest.raises(ValueError, match="retained authorization"):
        engine.propose(
            EngineStepRequest(
                state_version=_committed(prepared, revision=2),
                step_input=EffectSettled(
                    settlement=EffectSettlement.completed(
                        input_id="settlement-missing-authorization",
                        tool_call_id="call-missing-authorization",
                        tool_name="read",
                        effect_id=plan.effect_id,
                        attempt_id=plan.attempt_id,
                        authorization_transition_id="commit-2",
                        owner_epoch=3,
                        result={"content": "file contents"},
                    )
                ),
            )
        )


def test_engine_step_reentry_approval_denial_commits_tool_result_fact_and_continues_loop() -> (
    None
):
    engine = AgentEngine()
    initial = _initial_proposal(engine)
    tool_call = ModelToolCall(
        tool_call_id="call-approval",
        name="shell",
        arguments={"command": "rm generated.tmp"},
        requires_approval=True,
        approval_request_id="approval-1",
    )
    prepared = engine.propose(
        EngineStepRequest(
            state_version=_committed(initial, revision=1),
            step_input=ModelGenerationCompleted(result=_model_result(tool_call)),
        )
    )
    plan = prepared.effect_plans[0]
    denial = ApprovalSettlement(
        input_id="approval-denial-1",
        command_id="approval-command-1",
        tool_call_id="call-approval",
        tool_name="shell",
        effect_id=plan.effect_id,
        attempt_id=plan.attempt_id,
        transition_id="commit-2",
        owner_epoch=3,
        approved=False,
        rejection_reason_code="user_denied",
        rejection_message="User denied this tool call",
    )

    proposal = engine.propose(
        EngineStepRequest(
            state_version=_committed(prepared, revision=2),
            step_input=ApprovalResolved(settlement=denial),
        )
    )

    assert len(proposal.pending_facts) == 1
    assert proposal.pending_facts[0].fact_kind == "tool_result"
    assert proposal.pending_facts[0].payload["is_error"] is True
    assert proposal.pending_facts[0].payload["reason_code"] == "user_denied"
    assert isinstance(proposal.next_action, ModelGenerationAction)
    assert proposal.dispositions[0].command_id == "approval-command-1"


def test_next_model_request_contains_committed_assistant_tool_and_result() -> None:
    engine = AgentEngine()
    initial = _initial_proposal(engine)
    tool_call = ModelToolCall(
        tool_call_id="call-1",
        name="read",
        arguments={"path": "README.md"},
    )
    prepared = engine.propose(
        EngineStepRequest(
            state_version=_committed(initial, revision=1),
            step_input=ModelGenerationCompleted(result=_model_result(tool_call)),
        )
    )
    plan = prepared.effect_plans[0]
    settled = engine.propose(
        EngineStepRequest(
            state_version=_authorized_effect_state(
                prepared,
                revision=2,
                authorization_transition_id="commit-2",
                owner_epoch=3,
            ),
            step_input=EffectSettled(
                settlement=EffectSettlement.completed(
                    input_id="settlement-conversation-1",
                    tool_call_id="call-1",
                    tool_name="read",
                    effect_id=plan.effect_id,
                    attempt_id=plan.attempt_id,
                    authorization_transition_id="commit-2",
                    owner_epoch=3,
                    result={"content": "file contents"},
                )
            ),
        )
    )

    assert isinstance(settled.next_action, ModelGenerationAction)
    messages = settled.next_action.request.context["messages"]
    assert messages == (
        {
            "role": "assistant",
            "content": "assistant answer",
            "tool_calls": (
                {
                    "tool_call_id": "call-1",
                    "name": "read",
                    "arguments": {"path": "README.md"},
                },
            ),
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "read",
            "content": {"content": "file contents"},
            "is_error": False,
        },
    )


def test_transition_identity_is_stable_for_same_consume_once_input() -> None:
    engine = AgentEngine()
    step_input = Initial(
        input_id="initial-stable",
        command_batch=(),
        mailbox_cut=0,
    )

    first = engine.propose(
        EngineStepRequest(state_version=_state(revision=0), step_input=step_input)
    )
    replay_after_other_commits = engine.propose(
        EngineStepRequest(state_version=_state(revision=8), step_input=step_input)
    )

    assert first.transition_id == replay_after_other_commits.transition_id


def _prepared_effect_state() -> tuple[AgentEngine, object, object]:
    engine = AgentEngine()
    initial = _initial_proposal(engine)
    prepared = engine.propose(
        EngineStepRequest(
            state_version=_committed(initial, revision=1),
            step_input=ModelGenerationCompleted(
                result=_model_result(
                    ModelToolCall(
                        tool_call_id="call-recovery",
                        name="read",
                        arguments={"path": "README.md"},
                    )
                )
            ),
        )
    )
    plan = prepared.effect_plans[0]
    runtime = dict(prepared.state_value["_agentkit_runtime"])
    runtime["active_effect_authorization"] = {
        "effect_id": plan.effect_id,
        "attempt_id": plan.attempt_id,
        "tool_call_id": "call-recovery",
        "tool_name": "read",
        "authorization_transition_id": "authorization-recovery",
        "dispatch_owner_epoch": 3,
    }
    state = _state(
        revision=3,
        value={**prepared.state_value, "_agentkit_runtime": runtime},
    )
    return engine, plan, state


def test_indeterminate_settlement_keeps_pending_plan_and_commits_no_final_tool_fact() -> (
    None
):
    engine, plan, state = _prepared_effect_state()
    settlement = EffectSettlement(
        input_id="indeterminate-recovery",
        tool_call_id="call-recovery",
        tool_name="read",
        effect_id=plan.effect_id,
        attempt_id=plan.attempt_id,
        authorization_transition_id="authorization-recovery",
        owner_epoch=3,
        outcome=EffectSettlementOutcome.INDETERMINATE,
        result={"evidence_ref": "evidence-pending"},
        reason_code="executor_uncertain",
        reason_message="executor outcome is unknown",
    )

    proposal = engine.propose(
        EngineStepRequest(
            state_version=state,
            step_input=EffectSettled(settlement=settlement),
        )
    )

    assert isinstance(proposal.next_action, BlockedAction)
    assert proposal.pending_facts == ()
    runtime = proposal.state_value["_agentkit_runtime"]
    assert len(runtime["pending_effect_plans"]) == 1
    assert runtime["unknown_effect"] == {
        "effect_id": plan.effect_id,
        "attempt_id": plan.attempt_id,
        "tool_call_id": "call-recovery",
        "tool_name": "read",
        "authorization_transition_id": "authorization-recovery",
        "dispatch_owner_epoch": 3,
        "indeterminate_input_id": "indeterminate-recovery",
    }
    assert len(proposal.state_value["context"]["messages"]) == 1


def test_terminal_unknown_settlement_requires_committed_reconciliation() -> None:
    engine, plan, state = _prepared_effect_state()
    indeterminate = engine.propose(
        EngineStepRequest(
            state_version=state,
            step_input=EffectSettled(
                settlement=EffectSettlement(
                    input_id="indeterminate-without-reconciliation",
                    tool_call_id="call-recovery",
                    tool_name="read",
                    effect_id=plan.effect_id,
                    attempt_id=plan.attempt_id,
                    authorization_transition_id="authorization-recovery",
                    owner_epoch=3,
                    outcome=EffectSettlementOutcome.INDETERMINATE,
                    result=None,
                    reason_code="executor_uncertain",
                    reason_message="executor outcome is unknown",
                )
            ),
        )
    )

    with pytest.raises(ValueError, match="requires committed reconciliation"):
        engine.propose(
            EngineStepRequest(
                state_version=_state(revision=4, value=dict(indeterminate.state_value)),
                step_input=EffectSettled(
                    settlement=EffectSettlement.completed(
                        input_id="terminal-without-reconciliation",
                        tool_call_id="call-recovery",
                        tool_name="read",
                        effect_id=plan.effect_id,
                        attempt_id=plan.attempt_id,
                        authorization_transition_id="authorization-recovery",
                        owner_epoch=3,
                        result={"content": "late"},
                    )
                ),
            )
        )


def test_reconciled_effect_commits_exactly_one_final_tool_result_fact() -> None:
    engine, plan, state = _prepared_effect_state()
    runtime = dict(state.value["_agentkit_runtime"])
    runtime["unknown_effect"] = {
        **runtime["active_effect_authorization"],
        "indeterminate_input_id": "indeterminate-recovery",
    }
    runtime["reconciled_effect"] = {
        **runtime["unknown_effect"],
        "reconciliation_transition_id": "reconciliation-1",
        "evidence_ref": "evidence-1",
        "reconciliation_owner_epoch": 4,
        "reconciled_input_id": "reconciled-recovery",
        "outcome": "completed",
        "result": {"content": "recovered contents"},
        "reason_code": None,
        "reason_message": None,
    }
    reconciled_state = _state(
        revision=5,
        value={**state.value, "_agentkit_runtime": runtime},
    )
    settlement = EffectSettlement.completed(
        input_id="reconciled-recovery",
        tool_call_id="call-recovery",
        tool_name="read",
        effect_id=plan.effect_id,
        attempt_id=plan.attempt_id,
        authorization_transition_id="authorization-recovery",
        owner_epoch=5,
        result={"content": "recovered contents"},
    )

    proposal = engine.propose(
        EngineStepRequest(
            state_version=reconciled_state,
            step_input=EffectSettled(settlement=settlement),
        )
    )

    assert [fact.fact_kind for fact in proposal.pending_facts] == ["tool_result"]
    assert proposal.pending_facts[0].payload["result"] == {
        "content": "recovered contents"
    }
    committed_runtime = proposal.state_value["_agentkit_runtime"]
    assert committed_runtime["pending_effect_plans"] == ()
    assert "active_effect_authorization" not in committed_runtime
    assert "unknown_effect" not in committed_runtime
    assert "reconciled_effect" not in committed_runtime


def test_reconciled_marker_is_consumed_and_later_initial_is_accepted() -> None:
    engine, plan, state = _prepared_effect_state()
    runtime = dict(state.value["_agentkit_runtime"])
    unknown = {
        **runtime["active_effect_authorization"],
        "indeterminate_input_id": "indeterminate-recovery",
    }
    runtime["unknown_effect"] = unknown
    runtime["reconciled_effect"] = {
        **unknown,
        "reconciliation_transition_id": "reconciliation-1",
        "evidence_ref": "evidence-1",
        "reconciliation_owner_epoch": 4,
        "reconciled_input_id": "reconciled-recovery",
        "outcome": "completed",
        "result": {"content": "recovered contents"},
        "reason_code": None,
        "reason_message": None,
    }
    reconciled_state = _state(
        revision=5,
        value={**state.value, "_agentkit_runtime": runtime},
    )
    settlement = EffectSettlement.completed(
        input_id="reconciled-recovery",
        tool_call_id="call-recovery",
        tool_name="read",
        effect_id=plan.effect_id,
        attempt_id=plan.attempt_id,
        authorization_transition_id="authorization-recovery",
        owner_epoch=5,
        result={"content": "recovered contents"},
    )
    finalized = engine.propose(
        EngineStepRequest(
            state_version=reconciled_state,
            step_input=EffectSettled(settlement=settlement),
        )
    )
    finalized_state = _state(revision=6, value=finalized.state_value)

    proposal = engine.propose(
        EngineStepRequest(
            state_version=finalized_state,
            step_input=Initial(
                input_id="later-initial",
                command_batch=(),
                mailbox_cut=12,
            ),
        )
    )

    assert isinstance(proposal.next_action, ModelGenerationAction)
