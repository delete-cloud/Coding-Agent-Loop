from __future__ import annotations

import inspect

from agentkit.runtime import (
    AgentEngine,
    ApprovalResolved,
    ApprovalSettlement,
    CommitRef,
    EffectSettled,
    EffectSettlement,
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
            state_version=_committed(prepared, revision=2),
            step_input=EffectSettled(settlement=settlement),
        )
    )

    assert len(proposal.pending_facts) == 1
    assert proposal.pending_facts[0].fact_kind == "tool_result"
    assert proposal.pending_facts[0].payload["tool_call_id"] == "call-1"
    assert proposal.pending_facts[0].payload["result"] == {"content": "file contents"}
    assert isinstance(proposal.next_action, ModelGenerationAction)


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
