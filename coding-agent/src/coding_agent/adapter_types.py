"""Adapter types for Pipeline-to-CLI bridge."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StopReason(Enum):
    """Enumeration of possible reasons for stopping agent execution."""

    NO_TOOL_CALLS = "no_tool_calls"
    MAX_STEPS_REACHED = "max_steps_reached"
    DOOM_LOOP = "doom_loop"
    ERROR = "error"
    INTERRUPTED = "interrupted"


@dataclass
class TurnOutcome:
    """Result of a pipeline turn execution.

    This dataclass bridges the Pipeline (which returns PipelineContext) and CLI
    (which expects structured outcome information).
    """

    stop_reason: StopReason
    final_message: str | None = None
    steps_taken: int = 0
    error: str | None = None
