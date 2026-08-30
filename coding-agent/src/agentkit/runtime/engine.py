"""Persistence-free AgentKit state transition engine."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contracts import (
    ApprovalResolved,
    AppliedCommandDisposition,
    BlockedAction,
    EffectReference,
    EffectPlan,
    EffectSettled,
    EffectSettlementOutcome,
    EngineStepRequest,
    Initial,
    ModelGenerationAction,
    ModelGenerationCompleted,
    ModelGenerationResult,
    ModelRequest,
    PendingFact,
    PreparedEffectAction,
    PreparedEffectActionKind,
    RejectedCommandDisposition,
    RuntimeCommand,
    TerminalAction,
    TransitionProposal,
)

_RUNTIME_STATE_KEY = "_agentkit_runtime"


class AgentEngine:
    """Calculate one deterministic proposal without performing adapter or host I/O."""

    __slots__ = ()

    def propose(self, request: EngineStepRequest) -> TransitionProposal:
        if not isinstance(request, EngineStepRequest):
            raise TypeError("request must be an EngineStepRequest")

        step_input = request.step_input
        if isinstance(step_input, Initial):
            return self._propose_initial(request, step_input)
        if isinstance(step_input, ModelGenerationCompleted):
            return self._propose_model_completion(request, step_input)
        if isinstance(step_input, EffectSettled):
            return self._propose_effect_settlement(request, step_input)
        if isinstance(step_input, ApprovalResolved):
            return self._propose_approval_resolution(request, step_input)
        raise TypeError("unsupported engine step input")

    def _propose_initial(
        self,
        request: EngineStepRequest,
        step_input: Initial,
    ) -> TransitionProposal:
        transition_id = _transition_id(request)
        runtime_state = {
            "round_index": 0,
            "mailbox_cut": step_input.mailbox_cut,
            "commands": tuple(
                _command_value(command) for command in step_input.command_batch
            ),
            "pending_effect_plans": (),
        }
        state_value = _with_runtime_state(request.state_version.value, runtime_state)
        state_value, model_request = _activate_model_request(
            request.state_version.run_id,
            state_value,
        )
        return TransitionProposal(
            transition_id=transition_id,
            state_value=state_value,
            next_action=ModelGenerationAction(request=model_request),
        )

    def _propose_model_completion(
        self,
        request: EngineStepRequest,
        step_input: ModelGenerationCompleted,
    ) -> TransitionProposal:
        result = step_input.result
        transition_id = _transition_id(request)
        runtime_state = _runtime_state(request.state_version.value)
        _require_active_model_request(runtime_state, result.request_id)
        round_index = _runtime_int(runtime_state, "round_index") + 1
        facts = [
            PendingFact(
                fact_id=f"{transition_id}:assistant",
                fact_kind="assistant_message",
                payload={"content": result.assistant_content},
            )
        ]
        if result.finalized_thinking is not None:
            facts.append(
                PendingFact(
                    fact_id=f"{transition_id}:thinking",
                    fact_kind="finalized_thinking",
                    payload={"text": result.finalized_thinking},
                )
            )

        plans: list[EffectPlan] = []
        for index, call in enumerate(result.tool_calls):
            plan = EffectPlan(
                effect_id=f"{transition_id}:effect:{index}",
                attempt_id=f"{transition_id}:effect:{index}:attempt:1",
                effect_kind="tool",
                payload={
                    "tool_call_id": call.tool_call_id,
                    "tool_name": call.name,
                    "arguments": call.arguments,
                },
                idempotency_key=call.idempotency_key,
                requires_approval=call.requires_approval,
                approval_request_id=call.approval_request_id,
            )
            plans.append(plan)
            facts.append(
                PendingFact(
                    fact_id=f"{transition_id}:tool-call:{index}",
                    fact_kind="tool_call",
                    payload={
                        "tool_call_id": call.tool_call_id,
                        "tool_name": call.name,
                        "arguments": call.arguments,
                        "effect_id": plan.effect_id,
                        "attempt_id": plan.attempt_id,
                    },
                )
            )
            if call.requires_approval:
                facts.append(
                    PendingFact(
                        fact_id=f"{transition_id}:approval-request:{index}",
                        fact_kind="approval_requested",
                        payload={
                            "approval_request_id": call.approval_request_id,
                            "tool_call_id": call.tool_call_id,
                            "tool_name": call.name,
                            "arguments": call.arguments,
                            "effect_id": plan.effect_id,
                            "attempt_id": plan.attempt_id,
                        },
                    )
                )

        updated_runtime = dict(runtime_state)
        del updated_runtime["active_model_request_id"]
        updated_runtime["round_index"] = round_index
        updated_runtime["pending_effect_plans"] = tuple(
            _effect_plan_value(plan) for plan in plans
        )
        updated_runtime["last_assistant_content"] = result.assistant_content
        state_value = _with_runtime_state(request.state_version.value, updated_runtime)
        state_value = _append_conversation_messages(
            state_value,
            (_assistant_message(result),),
        )
        if plans:
            next_action = _prepared_action(plans[0])
        else:
            next_action = TerminalAction(
                final_message=result.assistant_content,
                stop_reason="no_tool_calls",
            )
        return TransitionProposal(
            transition_id=transition_id,
            state_value=state_value,
            next_action=next_action,
            pending_facts=tuple(facts),
            effect_plans=tuple(plans),
        )

    def _propose_effect_settlement(
        self,
        request: EngineStepRequest,
        step_input: EffectSettled,
    ) -> TransitionProposal:
        settlement = step_input.settlement
        transition_id = _transition_id(request)
        runtime_state = _runtime_state(request.state_version.value)
        plans = _pending_effect_plans(runtime_state)
        if (
            settlement.authorization_transition_id
            != request.state_version.commit_ref.transition_id
        ):
            raise ValueError(
                "effect settlement authorization_transition_id must match "
                "owning committed transition"
            )
        _require_next_effect(
            plans,
            effect_id=settlement.effect_id,
            attempt_id=settlement.attempt_id,
            tool_call_id=settlement.tool_call_id,
            tool_name=settlement.tool_name,
        )
        remaining = plans[1:]
        is_error = settlement.outcome is not EffectSettlementOutcome.COMPLETED
        payload: dict[str, Any] = {
            "tool_call_id": settlement.tool_call_id,
            "tool_name": settlement.tool_name,
            "effect_id": settlement.effect_id,
            "attempt_id": settlement.attempt_id,
            "result": settlement.result,
            "is_error": is_error,
            "outcome": settlement.outcome.value,
        }
        if settlement.reason_code is not None:
            payload["reason_code"] = settlement.reason_code
        if settlement.reason_message is not None:
            payload["reason_message"] = settlement.reason_message

        updated_runtime = dict(runtime_state)
        updated_runtime["pending_effect_plans"] = tuple(
            _effect_plan_value(plan) for plan in remaining
        )
        state_value = _with_runtime_state(request.state_version.value, updated_runtime)
        state_value = _append_conversation_messages(
            state_value,
            (
                {
                    "role": "tool",
                    "tool_call_id": settlement.tool_call_id,
                    "name": settlement.tool_name,
                    "content": settlement.result,
                    "is_error": is_error,
                },
            ),
        )
        if settlement.outcome is EffectSettlementOutcome.INDETERMINATE:
            next_action = BlockedAction(
                reason="indeterminate_dispatch",
                effect=_effect_reference(plans[0]),
            )
        elif remaining:
            next_action = _prepared_action(remaining[0])
        else:
            state_value, model_request = _activate_model_request(
                request.state_version.run_id,
                state_value,
            )
            next_action = ModelGenerationAction(request=model_request)
        return TransitionProposal(
            transition_id=transition_id,
            state_value=state_value,
            next_action=next_action,
            pending_facts=(
                PendingFact(
                    fact_id=f"{transition_id}:tool-result",
                    fact_kind="tool_result",
                    payload=payload,
                ),
            ),
        )

    def _propose_approval_resolution(
        self,
        request: EngineStepRequest,
        step_input: ApprovalResolved,
    ) -> TransitionProposal:
        settlement = step_input.settlement
        transition_id = _transition_id(request)
        runtime_state = _runtime_state(request.state_version.value)
        plans = _pending_effect_plans(runtime_state)
        if settlement.transition_id != request.state_version.commit_ref.transition_id:
            raise ValueError(
                "approval settlement transition_id must match owning committed "
                "transition"
            )
        _require_next_effect(
            plans,
            effect_id=settlement.effect_id,
            attempt_id=settlement.attempt_id,
            tool_call_id=settlement.tool_call_id,
            tool_name=settlement.tool_name,
        )
        plan = plans[0]
        if not plan.requires_approval:
            raise ValueError(
                "approval settlement does not match an approval-wait effect"
            )

        if settlement.approved:
            return TransitionProposal(
                transition_id=transition_id,
                state_value=request.state_version.value,
                next_action=PreparedEffectAction(
                    effect_plan=plan,
                    action_kind=PreparedEffectActionKind.DISPATCH,
                ),
                dispositions=(
                    AppliedCommandDisposition(command_id=settlement.command_id),
                ),
            )
        if settlement.rejection_reason_code is None:
            raise ValueError("approval denial requires a rejection reason code")

        remaining = plans[1:]
        updated_runtime = dict(runtime_state)
        updated_runtime["pending_effect_plans"] = tuple(
            _effect_plan_value(item) for item in remaining
        )
        state_value = _with_runtime_state(request.state_version.value, updated_runtime)
        state_value = _append_conversation_messages(
            state_value,
            (
                {
                    "role": "tool",
                    "tool_call_id": settlement.tool_call_id,
                    "name": settlement.tool_name,
                    "content": {
                        "reason_code": settlement.rejection_reason_code,
                        "message": settlement.rejection_message,
                    },
                    "is_error": True,
                },
            ),
        )
        if remaining:
            next_action = _prepared_action(remaining[0])
        else:
            state_value, model_request = _activate_model_request(
                request.state_version.run_id,
                state_value,
            )
            next_action = ModelGenerationAction(request=model_request)
        return TransitionProposal(
            transition_id=transition_id,
            state_value=state_value,
            next_action=next_action,
            pending_facts=(
                PendingFact(
                    fact_id=f"{transition_id}:tool-result",
                    fact_kind="tool_result",
                    payload={
                        "tool_call_id": settlement.tool_call_id,
                        "tool_name": settlement.tool_name,
                        "effect_id": settlement.effect_id,
                        "attempt_id": settlement.attempt_id,
                        "result": {
                            "reason_code": settlement.rejection_reason_code,
                            "message": settlement.rejection_message,
                        },
                        "is_error": True,
                        "reason_code": settlement.rejection_reason_code,
                    },
                ),
            ),
            dispositions=(
                RejectedCommandDisposition(
                    command_id=settlement.command_id,
                    reason_code=settlement.rejection_reason_code,
                ),
            ),
        )


def _transition_id(request: EngineStepRequest) -> str:
    step_input = request.step_input
    return (
        f"{request.state_version.run_id}:transition:"
        f"{type(step_input).__name__}:{step_input.input_id}"
    )


def _with_runtime_state(
    state_value: Mapping[str, Any],
    runtime_state: Mapping[str, Any],
) -> dict[str, Any]:
    updated = dict(state_value)
    updated[_RUNTIME_STATE_KEY] = dict(runtime_state)
    return updated


def _runtime_state(state_value: Mapping[str, Any]) -> dict[str, Any]:
    value = state_value.get(_RUNTIME_STATE_KEY)
    if value is None:
        return {
            "round_index": 0,
            "mailbox_cut": 0,
            "commands": (),
            "pending_effect_plans": (),
        }
    if not isinstance(value, Mapping):
        raise TypeError("engine runtime state must be a mapping")
    return dict(value)


def _runtime_int(runtime_state: Mapping[str, Any], key: str) -> int:
    value = runtime_state.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"engine runtime {key} must be a non-negative integer")
    return value


def _command_value(command: RuntimeCommand) -> dict[str, Any]:
    return {
        "command_id": command.command_id,
        "command_kind": command.command_kind,
        "payload": command.payload,
    }


def _commands(runtime_state: Mapping[str, Any]) -> tuple[RuntimeCommand, ...]:
    raw_commands = runtime_state.get("commands", ())
    if not isinstance(raw_commands, tuple | list):
        raise TypeError("engine runtime commands must be a sequence")
    commands: list[RuntimeCommand] = []
    for raw in raw_commands:
        if not isinstance(raw, Mapping):
            raise TypeError("engine runtime command must be a mapping")
        command_id = raw.get("command_id")
        command_kind = raw.get("command_kind")
        payload = raw.get("payload", {})
        if not isinstance(command_id, str) or not isinstance(command_kind, str):
            raise TypeError("engine runtime command identity must be a string")
        if not isinstance(payload, Mapping):
            raise TypeError("engine runtime command payload must be a mapping")
        commands.append(
            RuntimeCommand(
                command_id=command_id,
                command_kind=command_kind,
                payload=payload,
            )
        )
    return tuple(commands)


def _assistant_message(result: ModelGenerationResult) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": result.assistant_content,
        "tool_calls": tuple(
            {
                "tool_call_id": call.tool_call_id,
                "name": call.name,
                "arguments": call.arguments,
            }
            for call in result.tool_calls
        ),
    }


def _append_conversation_messages(
    state_value: Mapping[str, Any],
    appended: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    context = state_value.get("context", {})
    if not isinstance(context, Mapping):
        raise TypeError("state context must be a mapping")
    messages = context.get("messages", ())
    if not isinstance(messages, tuple | list):
        raise TypeError("state context messages must be a sequence")
    if any(not isinstance(message, Mapping) for message in messages):
        raise TypeError("state context messages must contain mappings")
    updated_context = dict(context)
    updated_context["messages"] = (*messages, *appended)
    updated_state = dict(state_value)
    updated_state["context"] = updated_context
    return updated_state


def _require_active_model_request(
    runtime_state: Mapping[str, Any],
    request_id: str,
) -> None:
    active_request_id = runtime_state.get("active_model_request_id")
    if not isinstance(active_request_id, str) or not active_request_id:
        raise ValueError("model completion requires an active model request")
    if request_id != active_request_id:
        raise ValueError(
            "model completion request_id must match the active model request"
        )


def _activate_model_request(
    run_id: str,
    state_value: Mapping[str, Any],
) -> tuple[dict[str, Any], ModelRequest]:
    model_request = _model_request(run_id, state_value)
    runtime_state = _runtime_state(state_value)
    runtime_state["active_model_request_id"] = model_request.request_id
    return _with_runtime_state(state_value, runtime_state), model_request


def _model_request(run_id: str, state_value: Mapping[str, Any]) -> ModelRequest:
    runtime_state = _runtime_state(state_value)
    round_index = _runtime_int(runtime_state, "round_index") + 1
    context = state_value.get("context", {})
    if not isinstance(context, Mapping):
        raise TypeError("state context must be a mapping")
    return ModelRequest(
        request_id=f"{run_id}:model:{round_index}",
        run_id=run_id,
        round_index=round_index,
        commands=_commands(runtime_state),
        context=context,
    )


def _effect_plan_value(plan: EffectPlan) -> dict[str, Any]:
    return {
        "effect_id": plan.effect_id,
        "attempt_id": plan.attempt_id,
        "effect_kind": plan.effect_kind,
        "payload": plan.payload,
        "idempotency_key": plan.idempotency_key,
        "requires_approval": plan.requires_approval,
        "approval_request_id": plan.approval_request_id,
    }


def _effect_plan(raw: Any) -> EffectPlan:
    if not isinstance(raw, Mapping):
        raise TypeError("pending effect plan must be a mapping")
    payload = raw.get("payload")
    if not isinstance(payload, Mapping):
        raise TypeError("pending effect plan payload must be a mapping")
    return EffectPlan(
        effect_id=_required_string(raw, "effect_id"),
        attempt_id=_required_string(raw, "attempt_id"),
        effect_kind=_required_string(raw, "effect_kind"),
        payload=payload,
        idempotency_key=_optional_string(raw, "idempotency_key"),
        requires_approval=_required_bool(raw, "requires_approval"),
        approval_request_id=_optional_string(raw, "approval_request_id"),
    )


def _pending_effect_plans(runtime_state: Mapping[str, Any]) -> tuple[EffectPlan, ...]:
    raw_plans = runtime_state.get("pending_effect_plans", ())
    if not isinstance(raw_plans, tuple | list):
        raise TypeError("pending effect plans must be a sequence")
    return tuple(_effect_plan(raw) for raw in raw_plans)


def _required_string(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"pending effect plan {key} must be non-empty")
    return value


def _optional_string(raw: Mapping[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"pending effect plan {key} must be non-empty when present")
    return value


def _required_bool(raw: Mapping[str, Any], key: str) -> bool:
    value = raw.get(key, False)
    if not isinstance(value, bool):
        raise TypeError(f"pending effect plan {key} must be a bool")
    return value


def _prepared_action(plan: EffectPlan) -> PreparedEffectAction:
    kind = (
        PreparedEffectActionKind.APPROVAL_WAIT
        if plan.requires_approval
        else PreparedEffectActionKind.DISPATCH
    )
    return PreparedEffectAction(effect_plan=plan, action_kind=kind)


def _effect_reference(plan: EffectPlan) -> EffectReference:
    return EffectReference.from_plan(plan)


def _require_next_effect(
    plans: tuple[EffectPlan, ...],
    *,
    effect_id: str,
    attempt_id: str,
    tool_call_id: str,
    tool_name: str,
) -> None:
    if not plans:
        raise ValueError("settlement has no pending effect plan")
    next_plan = plans[0]
    if next_plan.effect_id != effect_id:
        raise ValueError("settlement effect_id does not match next effect plan")
    if next_plan.attempt_id != attempt_id:
        raise ValueError("settlement attempt_id does not match next effect plan")
    if _required_string(next_plan.payload, "tool_call_id") != tool_call_id:
        raise ValueError("settlement tool_call_id does not match next effect plan")
    if _required_string(next_plan.payload, "tool_name") != tool_name:
        raise ValueError("settlement tool_name does not match next effect plan")
