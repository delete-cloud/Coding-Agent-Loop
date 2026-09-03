"""Version-fenced serving adapters for Phase F."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
import asyncio
import json
from typing import Any
from agentkit.providers.models import (
    DoneEvent,
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
    UsageEvent,
)
from agentkit.runtime import AgentEngine, SegmentCoordinator
from agentkit.runtime.contracts import (
    ApprovalResolved,
    ApprovalSettlement,
    BlockedOutcome,
    CommitRef,
    EffectReference,
    CommittedFactSink,
    ControlGeneration,
    ControlProbe,
    ControlSnapshot,
    FrameSink,
    Initial,
    ModelGenerationResult,
    ModelToolCall,
    ModelUsage,
    OperationStateVersion,
    ProviderStopMetadata,
    RunSegmentRequest,
    RoundLimitOutcome,
    RuntimeCommand,
    SegmentOutcome,
    StreamFrame,
    StreamFrameKind,
)
from coding_agent.approval import ApprovalPolicy, PolicyConfig, PolicyEngine
from coding_agent.executors.durable import (
    DurableEffectExecutor,
    LocalToolEffectBackend,
)
from coding_agent.plugins.core_tools import CoreToolExecutor
from coding_agent.runtime_activation import serving_turn_kind
from coding_agent.runs.turn_execution import DurableSegmentRunner
from coding_agent.server.stores.session_owner_store import OwnerAuthority
from coding_agent.stores.durable_commit_port import (
    PostgreSQLCommitPort,
    SQLiteCommitPort,
)
from coding_agent.stores.durable_local import SQLiteLocalDurableStore
from coding_agent.stores.durable_pg import PGDurableStore


ServingBlockedResolver = Callable[
    [BlockedOutcome],
    Awaitable[ApprovalSettlement],
]


@dataclass(frozen=True)
class DurableSegmentTurnAdapter:
    """Turn adapter that never uses PipelineAdapter for new-runtime sessions."""

    runner: DurableSegmentRunner
    session_id: str
    owner_id: str
    owner_epoch: int
    state_version: OperationStateVersion
    max_rounds: int
    control_probe: ControlProbe
    frame_sink: FrameSink
    committed_fact_sink: CommittedFactSink
    resolve_blocked: ServingBlockedResolver | None = None

    def _request(
        self,
        prompt: str,
        step_input: Initial | ApprovalResolved,
        *,
        max_rounds: int,
    ) -> RunSegmentRequest:
        state = self.state_version
        if isinstance(step_input, Initial) and prompt.strip():
            value = dict(state.value)
            context = dict(value.get("context") or {})
            messages = list(context.get("messages") or ())
            messages.append({"role": "user", "content": prompt})
            context["messages"] = tuple(messages)
            value["context"] = context
            state = replace(state, value=value)
        return RunSegmentRequest(
            session_id=self.session_id,
            owner_id=self.owner_id,
            owner_epoch=self.owner_epoch,
            state_version=state,
            step_input=step_input,
            max_rounds=max_rounds,
        )

    async def run_turn(self, prompt: str) -> SegmentOutcome:
        completed_rounds = _persisted_rounds(self.state_version)
        resume_blocked = blocked_approval_from_state(self.state_version)
        if resume_blocked is not None:
            remaining_rounds = self.max_rounds - completed_rounds
            if remaining_rounds <= 0:
                return RoundLimitOutcome(
                    state_version=self.state_version,
                    steps_taken=completed_rounds,
                )
            if self.resolve_blocked is None:
                return replace(resume_blocked, steps_taken=completed_rounds)
            settlement = await self.resolve_blocked(resume_blocked)
            request = self._request(
                "",
                ApprovalResolved(settlement=settlement),
                max_rounds=remaining_rounds,
            )
        else:
            if self.state_version.revision != 0:
                raise RuntimeError(
                    "new-runtime serving cannot restart a non-approval state"
                )
            request = self._request(
                prompt,
                Initial(
                    input_id=f"{self.state_version.run_id}:initial",
                    command_batch=(),
                    mailbox_cut=0,
                ),
                max_rounds=self.max_rounds,
            )

        while True:
            outcome = await self.runner.run(
                request,
                self.control_probe,
                self.frame_sink,
                self.committed_fact_sink,
            )
            if (
                not isinstance(outcome, BlockedOutcome)
                or outcome.reason != "approval_required"
                or outcome.effect is None
                or self.resolve_blocked is None
            ):
                if completed_rounds == 0:
                    return outcome
                return replace(
                    outcome,
                    steps_taken=completed_rounds + outcome.steps_taken,
                )
            completed_rounds += outcome.steps_taken
            remaining_rounds = self.max_rounds - completed_rounds
            if remaining_rounds <= 0:
                return RoundLimitOutcome(
                    state_version=outcome.state_version,
                    steps_taken=completed_rounds,
                )
            settlement = await self.resolve_blocked(outcome)
            request = replace(
                request,
                state_version=outcome.state_version,
                step_input=ApprovalResolved(settlement=settlement),
                max_rounds=remaining_rounds,
            )


def session_serving_turn_kind(session: Any) -> str:
    payload: Mapping[str, object] = {
        "runtime_version": getattr(session, "runtime_version", None),
    }
    return serving_turn_kind(payload)


class ServingControlProbe:
    def __init__(self) -> None:
        self._never = asyncio.Event()

    def observe(self) -> ControlSnapshot:
        return ControlSnapshot(generation=ControlGeneration(0), raised=False)

    async def wait(self, after: ControlGeneration) -> ControlSnapshot:
        del after
        await self._never.wait()
        return self.observe()


class ServingNullSink:
    async def emit(self, _item: object) -> None:
        return None


class ProviderModelAdapter:
    """Adapt an LLMProvider.stream surface into the frozen ModelAdapter."""

    def __init__(
        self,
        provider: Any,
        *,
        tools: tuple[Any, ...] = (),
        approval_policy: ApprovalPolicy = ApprovalPolicy.AUTO,
    ) -> None:
        self._provider = provider
        self._tools = tools
        self._approval_policy = approval_policy

    async def generate(
        self,
        request: Any,
        frame_sink: FrameSink,
        cancellation: object,
    ) -> ModelGenerationResult:
        del cancellation
        messages = _messages_from_model_request(request)
        texts: list[str] = []
        thinking: list[str] = []
        calls: list[ModelToolCall] = []
        input_tokens = 0
        output_tokens = 0
        frame_index = 0
        stream = self._provider.stream(
            messages,
            tools=list(self._tools) or None,
        )
        async for event in stream:
            if isinstance(event, TextEvent) and event.text:
                texts.append(event.text)
                await frame_sink.emit(
                    StreamFrame(
                        frame_id=f"{request.request_id}:frame:{frame_index}",
                        kind=StreamFrameKind.TOKEN_DELTA,
                        payload={"text": event.text},
                    )
                )
                frame_index += 1
            elif isinstance(event, ThinkingEvent) and event.text:
                thinking.append(event.text)
            elif isinstance(event, ToolCallEvent) and event.tool_call_id and event.name:
                requires_approval = PolicyEngine(
                    PolicyConfig(policy=self._approval_policy)
                ).needs_approval(event.name)
                calls.append(
                    ModelToolCall(
                        tool_call_id=event.tool_call_id,
                        name=event.name,
                        arguments=event.arguments or {},
                        requires_approval=requires_approval,
                        approval_request_id=(
                            f"{request.request_id}:{event.tool_call_id}"
                            if requires_approval
                            else None
                        ),
                    )
                )
            elif isinstance(event, UsageEvent):
                input_tokens = event.input_tokens
                output_tokens = event.output_tokens
            elif isinstance(event, DoneEvent):
                break
        return ModelGenerationResult(
            result_id=f"{request.request_id}:result",
            request_id=request.request_id,
            assistant_content="".join(texts),
            finalized_thinking="".join(thinking) or None,
            tool_calls=tuple(calls),
            usage=ModelUsage(input_tokens=input_tokens, output_tokens=output_tokens),
            provider_stop=ProviderStopMetadata(reason="tool_use" if calls else "stop"),
        )


def _messages_from_model_request(request: Any) -> list[dict[str, Any]]:
    context = getattr(request, "context", {})
    if not isinstance(context, Mapping):
        return []
    raw_messages = context.get("messages", ())
    messages: list[dict[str, Any]] = []
    if not isinstance(raw_messages, list | tuple):
        return messages
    for message in raw_messages:
        if not isinstance(message, Mapping):
            continue
        role = message.get("role") or "assistant"
        if role == "assistant":
            entry: dict[str, Any] = {
                "role": "assistant",
                "content": _message_text(message),
            }
            tool_calls = _openai_tool_calls(message.get("tool_calls"))
            if tool_calls:
                entry["tool_calls"] = tool_calls
            messages.append(entry)
        elif role == "tool":
            tool_call_id = message.get("tool_call_id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                raise ValueError("tool message requires tool_call_id")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": message.get("name") or "",
                    "content": _message_text(message),
                }
            )
        else:
            messages.append({"role": str(role), "content": _message_text(message)})
    return messages


def _message_text(message: Mapping[str, Any]) -> str:
    content = message.get("content")
    if content is None:
        content = message.get("text") or ""
    if isinstance(content, str):
        return content
    return json.dumps(content, default=str)


def _openai_tool_calls(raw_calls: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_calls, list | tuple):
        return []
    calls: list[dict[str, Any]] = []
    for raw in raw_calls:
        if not isinstance(raw, Mapping):
            continue
        call_id = raw.get("tool_call_id") or raw.get("id")
        name = raw.get("name")
        if not isinstance(call_id, str) or not call_id:
            raise ValueError("assistant tool_call requires tool_call_id")
        if not isinstance(name, str) or not name:
            raise ValueError("assistant tool_call requires name")
        arguments = raw.get("arguments") or {}
        calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": (
                        arguments
                        if isinstance(arguments, str)
                        else json.dumps(arguments, default=str)
                    ),
                },
            }
        )
    return calls


def commit_port_for_store(store: object, session_state: Mapping[str, object]) -> object:
    payload = dict(session_state)
    if isinstance(store, SQLiteLocalDurableStore):
        return SQLiteCommitPort(store, session_state=payload)
    if isinstance(store, PGDurableStore):
        return PostgreSQLCommitPort(store, session_state=payload)
    raise TypeError("new-runtime serving requires a durable SQLite or PostgreSQL store")


def session_workspace_root(session: Any) -> Path:
    target = getattr(session, "default_run_target", None)
    workspace = getattr(target, "workspace", None)
    path = getattr(workspace, "path", None)
    if isinstance(path, str) and path:
        return Path(path)
    raise ValueError("new-runtime serving requires a resolved workspace root")


_SERVING_EXCLUDED_TOOLS = frozenset({"subagent"})


def serving_tool_schemas(executor: CoreToolExecutor) -> tuple[Any, ...]:
    return tuple(
        schema
        for schema in executor.schemas()
        if schema.name not in _SERVING_EXCLUDED_TOOLS
    )


def session_tool_executor(session: Any) -> CoreToolExecutor:
    return CoreToolExecutor(workspace_root=session_workspace_root(session))


def session_model_adapter(session: Any, executor: CoreToolExecutor) -> object:
    provider = getattr(session, "provider", None)
    policy = getattr(session, "approval_policy", None) or ApprovalPolicy.AUTO
    if callable(getattr(provider, "generate", None)):
        return provider
    if callable(getattr(provider, "stream", None)):
        return ProviderModelAdapter(
            provider,
            tools=serving_tool_schemas(executor),
            approval_policy=policy,
        )
    raise RuntimeError("new-runtime serving requires a ModelAdapter")


def _persisted_rounds(state: OperationStateVersion) -> int:
    runtime = state.value.get("_agentkit_runtime")
    if runtime is None:
        return 0
    if not isinstance(runtime, Mapping):
        raise TypeError("engine runtime state must be a mapping")
    round_index = runtime.get("round_index", 0)
    if isinstance(round_index, bool) or not isinstance(round_index, int):
        raise TypeError("engine runtime round_index must be an integer")
    if round_index < 0:
        raise ValueError("engine runtime round_index must be non-negative")
    return round_index


def _pending_effect_plan(
    state: OperationStateVersion,
) -> Mapping[str, Any] | None:
    runtime = state.value.get("_agentkit_runtime")
    if runtime is None:
        return None
    if not isinstance(runtime, Mapping):
        raise TypeError("engine runtime state must be a mapping")
    plans = runtime.get("pending_effect_plans") or ()
    if not isinstance(plans, tuple | list):
        raise TypeError("pending effect plans must be a sequence")
    if not plans:
        return None
    plan = plans[0]
    if not isinstance(plan, Mapping):
        raise TypeError("pending effect plan must be a mapping")
    return plan


def _required_plan_string(plan: Mapping[str, Any], field_name: str) -> str:
    value = plan.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"pending effect plan requires {field_name}")
    return value


def blocked_approval_from_state(
    state: OperationStateVersion,
) -> BlockedOutcome | None:
    plan = _pending_effect_plan(state)
    if plan is None:
        return None
    requires_approval = plan.get("requires_approval", False)
    if not isinstance(requires_approval, bool):
        raise TypeError("pending effect plan requires_approval must be a bool")
    if not requires_approval:
        return None
    payload = plan.get("payload")
    if not isinstance(payload, Mapping):
        raise TypeError("pending effect plan payload must be a mapping")
    tool_call_id = _required_plan_string(payload, "tool_call_id")
    approval_request_id = plan.get("approval_request_id")
    if approval_request_id is not None and (
        not isinstance(approval_request_id, str) or not approval_request_id
    ):
        raise ValueError("pending effect plan approval_request_id must be non-empty")
    return BlockedOutcome(
        state_version=state,
        reason="approval_required",
        effect=EffectReference(
            effect_id=_required_plan_string(plan, "effect_id"),
            attempt_id=_required_plan_string(plan, "attempt_id"),
            tool_call_id=tool_call_id,
            approval_request_id=approval_request_id,
        ),
        steps_taken=0,
    )


def approval_settlement_from_mailbox(
    blocked: BlockedOutcome,
    mailbox: Any,
    *,
    owner_epoch: int,
) -> ApprovalSettlement | None:
    if blocked.effect is None:
        raise ValueError("blocked approval outcome requires an effect")
    approval_request_id = (
        blocked.effect.approval_request_id or blocked.effect.tool_call_id
    )
    command_id, input_id = serving_approval_identity(
        run_id=blocked.state_version.run_id,
        request_id=approval_request_id,
    )
    for entry in mailbox.entries:
        command = entry.command
        if command.command_id != command_id:
            continue
        if command.command_kind != "approval_decision":
            raise ValueError("serving approval command has the wrong kind")
        payload = command.payload
        approved = payload.get("approved")
        if not isinstance(approved, bool):
            raise TypeError("serving approval command approved must be a bool")
        if payload.get("request_id") != input_id:
            raise ValueError("serving approval command request_id does not match")
        if payload.get("target_run_id") != blocked.state_version.run_id:
            raise ValueError("serving approval command target_run_id does not match")
        return approval_settlement_from_blocked(
            blocked,
            approved=approved,
            owner_epoch=owner_epoch,
            command_id=command_id,
        )
    return None


def serving_approval_identity(
    *,
    run_id: str,
    request_id: str,
) -> tuple[str, str]:
    if not run_id:
        raise ValueError("serving approval requires a run_id")
    if not request_id:
        raise ValueError("serving approval requires a request_id")
    command_id = f"serving-approval:{request_id}"
    return command_id, f"{run_id}:approval:{command_id}"


def approval_settlement_from_blocked(
    blocked: BlockedOutcome,
    *,
    approved: bool,
    owner_epoch: int,
    command_id: str | None = None,
) -> ApprovalSettlement:
    if blocked.effect is None:
        raise ValueError("blocked approval outcome requires an effect")
    request_id = blocked.effect.approval_request_id or blocked.effect.tool_call_id
    default_command_id, _input_id = serving_approval_identity(
        run_id=blocked.state_version.run_id,
        request_id=request_id,
    )
    resolved_command_id = command_id or default_command_id
    input_id = f"{blocked.state_version.run_id}:approval:{resolved_command_id}"
    tool_name, _arguments = blocked_approval_tool(blocked)
    return ApprovalSettlement(
        input_id=input_id,
        command_id=resolved_command_id,
        tool_call_id=blocked.effect.tool_call_id,
        tool_name=tool_name,
        effect_id=blocked.effect.effect_id,
        attempt_id=blocked.effect.attempt_id,
        transition_id=blocked.state_version.commit_ref.transition_id,
        owner_epoch=owner_epoch,
        approved=approved,
        rejection_reason_code=None if approved else "user_denied",
        rejection_message=None if approved else "User denied this tool call",
    )


async def admit_blocked_approval(
    store: Any,
    authority: OwnerAuthority,
    blocked: BlockedOutcome,
    *,
    approved: bool,
) -> ApprovalSettlement:
    settlement = approval_settlement_from_blocked(
        blocked,
        approved=approved,
        owner_epoch=authority.epoch,
    )
    await store.admit_new_runtime_command(
        authority,
        RuntimeCommand(
            command_id=settlement.command_id,
            command_kind="approval_decision",
            payload={
                "approved": approved,
                "request_id": settlement.input_id,
                "target_run_id": blocked.state_version.run_id,
            },
        ),
    )
    return settlement


def blocked_approval_tool(blocked: BlockedOutcome) -> tuple[str, dict[str, Any]]:
    payload = _blocked_payload(blocked)
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError("pending effect plan requires a tool_name")
    raw_arguments = payload.get("arguments") or {}
    if not isinstance(raw_arguments, Mapping):
        raise TypeError("pending effect plan arguments must be a mapping")
    return tool_name, {str(key): value for key, value in raw_arguments.items()}


def _blocked_payload(blocked: BlockedOutcome) -> Mapping[str, Any]:
    plan = _pending_effect_plan(blocked.state_version)
    if plan is None:
        raise ValueError("blocked state has no pending effect plan")
    payload = plan.get("payload")
    if not isinstance(payload, Mapping):
        raise TypeError("pending effect plan payload must be a mapping")
    return payload


def initial_operation_state(
    run_id: str,
    *,
    system_prompt: str,
) -> OperationStateVersion:
    return OperationStateVersion(
        run_id=run_id,
        revision=0,
        projection_epoch=0,
        commit_ref=CommitRef(transition_id=f"{run_id}:admission"),
        value={
            "context": {
                "messages": ({"role": "system", "content": system_prompt},),
            },
        },
    )


def build_new_runtime_turn_adapter(
    *,
    session: Any,
    run_id: str,
    authority: OwnerAuthority,
    store: object,
    state_version: OperationStateVersion | None,
    system_prompt: str,
    model_adapter: object | None = None,
    effect_executor: object | None = None,
    resolve_blocked: ServingBlockedResolver | None = None,
) -> DurableSegmentTurnAdapter:
    session_state: Mapping[str, object]
    to_store_data = getattr(session, "to_store_data", None)
    if callable(to_store_data):
        session_state = dict(to_store_data())
    else:
        session_state = {
            "id": authority.session_id,
            "session_id": authority.session_id,
        }
    if session_state.get("id") != authority.session_id:
        session_state = {**session_state, "id": authority.session_id}
    commit_port = commit_port_for_store(store, session_state)
    tool_executor = session_tool_executor(session)
    executor = effect_executor or DurableEffectExecutor(
        store,  # type: ignore[arg-type]
        owner_id=authority.owner_id,
        executor_id="local-daemon",
        backend=LocalToolEffectBackend(tool_executor),
        reservation_lease=timedelta(seconds=30),
    )
    coordinator = SegmentCoordinator(
        engine=AgentEngine(),
        model_adapter=(
            model_adapter
            if model_adapter is not None
            else session_model_adapter(session, tool_executor)
        ),
        commit_port=commit_port,
        effect_executor=executor,
    )
    runner = DurableSegmentRunner(coordinator=coordinator, commit_port=commit_port)
    resolved_state = state_version or initial_operation_state(
        run_id,
        system_prompt=system_prompt,
    )
    return DurableSegmentTurnAdapter(
        runner=runner,
        session_id=authority.session_id,
        owner_id=authority.owner_id,
        owner_epoch=authority.epoch,
        state_version=resolved_state,
        max_rounds=getattr(session, "max_steps", None) or 30,
        control_probe=ServingControlProbe(),
        frame_sink=ServingNullSink(),
        committed_fact_sink=ServingNullSink(),
        resolve_blocked=resolve_blocked,
    )
