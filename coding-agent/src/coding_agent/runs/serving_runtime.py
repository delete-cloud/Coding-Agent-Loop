"""Version-fenced serving adapters for Phase F."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
import asyncio
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
    CommitRef,
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
    SegmentOutcome,
    StreamFrame,
    StreamFrameKind,
)
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


@dataclass(frozen=True)
class DurableSegmentTurnAdapter:
    """Turn adapter that never uses PipelineAdapter for new-runtime sessions."""

    runner: DurableSegmentRunner
    request_for_prompt: Callable[[str], RunSegmentRequest]
    control_probe: ControlProbe
    frame_sink: FrameSink
    committed_fact_sink: CommittedFactSink

    async def run_turn(self, prompt: str) -> SegmentOutcome:
        return await self.runner.run(
            self.request_for_prompt(prompt),
            self.control_probe,
            self.frame_sink,
            self.committed_fact_sink,
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


class UnwiredServingModelAdapter:
    async def generate(
        self,
        request: object,
        frame_sink: object,
        cancellation: object,
    ) -> object:
        del request, frame_sink, cancellation
        raise RuntimeError("new-runtime serving requires a ModelAdapter")


class ProviderModelAdapter:
    """Adapt an LLMProvider.stream surface into the frozen ModelAdapter."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider

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
        async for event in self._provider.stream(messages):
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
                calls.append(
                    ModelToolCall(
                        tool_call_id=event.tool_call_id,
                        name=event.name,
                        arguments=event.arguments or {},
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
    if isinstance(raw_messages, list | tuple):
        for message in raw_messages:
            if not isinstance(message, Mapping):
                continue
            role = message.get("role") or "assistant"
            content = message.get("content")
            if content is None:
                content = message.get("text") or ""
            messages.append({"role": str(role), "content": str(content)})
    return messages


def commit_port_for_store(store: object, session_state: Mapping[str, object]) -> object:
    payload = dict(session_state)
    if isinstance(store, SQLiteLocalDurableStore):
        return SQLiteCommitPort(store, session_state=payload)
    if isinstance(store, PGDurableStore):
        return PostgreSQLCommitPort(store, session_state=payload)
    raise TypeError("new-runtime serving requires a durable SQLite or PostgreSQL store")


def session_model_adapter(session: Any) -> object:
    provider = getattr(session, "provider", None)
    if callable(getattr(provider, "generate", None)):
        return provider
    if callable(getattr(provider, "stream", None)):
        return ProviderModelAdapter(provider)
    return UnwiredServingModelAdapter()


def session_effect_backend(session: Any) -> LocalToolEffectBackend:
    repo_path = getattr(session, "repo_path", None)
    workspace = Path(repo_path) if repo_path is not None else Path(".")
    return LocalToolEffectBackend(CoreToolExecutor(workspace_root=workspace))


def initial_operation_state(run_id: str) -> OperationStateVersion:
    return OperationStateVersion(
        run_id=run_id,
        revision=0,
        projection_epoch=0,
        commit_ref=CommitRef(transition_id=f"{run_id}:admission"),
        value={},
    )


def build_new_runtime_turn_adapter(
    *,
    session: Any,
    run_id: str,
    authority: OwnerAuthority,
    store: object,
    state_version: OperationStateVersion | None,
    model_adapter: object | None = None,
    effect_executor: object | None = None,
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
    executor = effect_executor or DurableEffectExecutor(
        store,  # type: ignore[arg-type]
        owner_id=authority.owner_id,
        executor_id="local-daemon",
        backend=session_effect_backend(session),
        reservation_lease=timedelta(seconds=30),
    )
    coordinator = SegmentCoordinator(
        engine=AgentEngine(),
        model_adapter=model_adapter or session_model_adapter(session),
        commit_port=commit_port,
        effect_executor=executor,
    )
    runner = DurableSegmentRunner(coordinator=coordinator, commit_port=commit_port)
    resolved_state = state_version or initial_operation_state(run_id)
    max_rounds = getattr(session, "max_steps", None) or 30

    def request_for_prompt(prompt: str) -> RunSegmentRequest:
        state = resolved_state
        if prompt.strip():
            value = dict(state.value)
            context = dict(value.get("context") or {})
            messages = list(context.get("messages") or ())
            messages.append({"role": "user", "content": prompt})
            context["messages"] = tuple(messages)
            value["context"] = context
            state = replace(state, value=value)
        return RunSegmentRequest(
            session_id=authority.session_id,
            owner_id=authority.owner_id,
            owner_epoch=authority.epoch,
            state_version=state,
            step_input=Initial(
                input_id=f"{run_id}:initial",
                command_batch=(),
                mailbox_cut=0,
            ),
            max_rounds=max_rounds,
        )

    return DurableSegmentTurnAdapter(
        runner=runner,
        request_for_prompt=request_for_prompt,
        control_probe=ServingControlProbe(),
        frame_sink=ServingNullSink(),
        committed_fact_sink=ServingNullSink(),
    )
