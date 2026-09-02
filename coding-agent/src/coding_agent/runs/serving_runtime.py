"""Version-fenced serving adapters for Phase F."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from agentkit.runtime.contracts import (
    CommittedFactSink,
    ControlProbe,
    FrameSink,
    RunSegmentRequest,
    SegmentOutcome,
)
from coding_agent.runtime_activation import serving_turn_kind
from coding_agent.runs.turn_execution import DurableSegmentRunner


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
