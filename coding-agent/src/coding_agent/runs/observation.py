from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from agentkit.observability import ObservationSink
from agentkit.tape.extract import TurnTrace, extract_turns

from coding_agent.agent_observability import (
    AgentObservationRecorder,
    AgentObservationStatus,
    AgentObservationStore,
)
from coding_agent.environment.execution_binding import ExecutionBinding
from coding_agent.runtime_store import JSONObject
from coding_agent.runs.lifecycle import RuntimeRunResumeContext
from coding_agent.runs.metadata import runtime_execution_placement


class RuntimeObservationSession(Protocol):
    id: str
    execution_binding: ExecutionBinding


@dataclass(frozen=True)
class RuntimeObservationService:
    store: AgentObservationStore

    def start(
        self,
        *,
        session: RuntimeObservationSession,
        ctx: Any,
        run_id: str,
        prompt: str,
        resume_context: RuntimeRunResumeContext | None = None,
    ) -> AgentObservationRecorder | None:
        config = getattr(ctx, "config", None)
        if not isinstance(config, dict):
            return None
        recorder = AgentObservationRecorder(
            store=self.store,
            sink=_observation_sink(ctx),
        )
        config["agent_observation_recorder"] = recorder
        recorder.start_turn(
            session_id=session.id,
            run_id=run_id,
            prompt=prompt,
            attributes=_observation_attributes(
                session,
                ctx,
                resume_context=resume_context,
            ),
        )
        return recorder

    def complete(
        self,
        recorder: AgentObservationRecorder | None,
        *,
        ctx: Any,
        turn_status: str,
    ) -> None:
        if recorder is None:
            return
        recorder.complete_turn(
            status=_observation_status(turn_status),
            turn=_latest_turn_trace(ctx),
        )


def _observation_attributes(
    session: RuntimeObservationSession,
    ctx: Any,
    *,
    resume_context: RuntimeRunResumeContext | None = None,
) -> JSONObject:
    binding = session.execution_binding
    attributes: JSONObject = {
        "tape_id": getattr(getattr(ctx, "tape", None), "tape_id", None),
        "execution_placement": runtime_execution_placement(binding),
        "execution_binding_kind": binding.kind,
        "workspace_surface": binding.workspace_surface,
        "execution_plane": binding.execution_plane,
    }
    if resume_context is not None:
        attributes.update(resume_context.metadata())
    return attributes

def _observation_status(turn_status: str) -> AgentObservationStatus:
    if turn_status in {"cancelled", "interrupted"}:
        return "cancelled"
    if turn_status == "failed":
        return "error"
    return "ok"


def _latest_turn_trace(ctx: Any) -> TurnTrace | None:
    tape = getattr(ctx, "tape", None)
    if tape is None or not hasattr(tape, "snapshot"):
        return None
    turns = extract_turns(tape.snapshot())
    if not turns:
        return None
    return turns[-1]


def _observation_sink(ctx: Any) -> ObservationSink | None:
    config = getattr(ctx, "config", None)
    if not isinstance(config, dict):
        return None
    sink = config.get("observation_sink")
    if isinstance(sink, ObservationSink):
        return sink
    return None


__all__ = [
    "RuntimeObservationService",
    "RuntimeObservationSession",
]
