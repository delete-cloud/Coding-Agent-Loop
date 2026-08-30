"""Adapter types for Pipeline-to-CLI bridge."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from agentkit.runtime.contracts import (
    BlockedOutcome,
    CancelledOutcome,
    CompletedOutcome,
    FailedOutcome,
    RoundLimitOutcome,
    SafeYieldOutcome,
    SegmentOutcome,
)


class StopReason(Enum):
    """Enumeration of possible reasons for stopping agent execution."""

    NO_TOOL_CALLS = "no_tool_calls"
    MAX_STEPS_REACHED = "max_steps_reached"
    DOOM_LOOP = "doom_loop"
    ERROR = "error"
    INTERRUPTED = "interrupted"


type DurableRootStatus = Literal["completed", "failed", "interrupted", "cancelled"]


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
    durable_root_status: DurableRootStatus | None = None

    def __post_init__(self) -> None:
        if self.durable_root_status not in {
            None,
            "completed",
            "failed",
            "interrupted",
            "cancelled",
        }:
            raise ValueError("durable_root_status must be a durable root status")


def exception_error_message(exc: BaseException) -> str:
    """Return a non-empty, user-informative message for an exception.

    Some exceptions (e.g. bare ``RuntimeError()`` or framework wrappers) have
    an empty ``str()``. Persisting that empty string as a run/turn error trips
    the runtime store's non-empty validation and masks the real failure, so
    fall back to the exception type name (plus the wrapped cause, when the
    exception chains one) when the message is blank.
    """

    message = str(exc)
    if message.strip():
        return message
    cause = exc.__cause__
    if cause is not None:
        return f"{type(exc).__name__}: {exception_error_message(cause)}"
    return type(exc).__name__


def stop_reason_from_segment_outcome(
    outcome: SegmentOutcome,
    *,
    doom_loop: bool = False,
) -> StopReason | None:
    """Map segment control flow to the legacy stop signal without settling it."""

    if isinstance(outcome, CompletedOutcome):
        return StopReason.DOOM_LOOP if doom_loop else StopReason.NO_TOOL_CALLS
    if isinstance(outcome, RoundLimitOutcome):
        return StopReason.MAX_STEPS_REACHED
    if isinstance(outcome, FailedOutcome):
        return StopReason.ERROR
    if isinstance(outcome, CancelledOutcome):
        return StopReason.INTERRUPTED
    if isinstance(outcome, SafeYieldOutcome):
        return StopReason.INTERRUPTED if outcome.reason == "interrupt" else None
    if isinstance(outcome, BlockedOutcome):
        return None
    raise TypeError("unsupported segment outcome")


def turn_outcome_from_segment_outcome(
    outcome: SegmentOutcome,
    *,
    doom_loop: bool = False,
) -> TurnOutcome | None:
    """Map a settled Phase C outcome to the existing product turn contract."""

    if isinstance(outcome, CompletedOutcome):
        stop_reason = StopReason.DOOM_LOOP if doom_loop else StopReason.NO_TOOL_CALLS
        return TurnOutcome(
            stop_reason=stop_reason,
            final_message=outcome.final_message,
            steps_taken=outcome.steps_taken,
        )
    if isinstance(outcome, RoundLimitOutcome):
        return TurnOutcome(
            stop_reason=StopReason.MAX_STEPS_REACHED,
            steps_taken=outcome.steps_taken,
        )
    if isinstance(outcome, FailedOutcome):
        return TurnOutcome(
            stop_reason=StopReason.ERROR,
            steps_taken=outcome.steps_taken,
            error=outcome.error.message,
        )
    if isinstance(outcome, CancelledOutcome):
        return TurnOutcome(
            stop_reason=StopReason.INTERRUPTED,
            steps_taken=outcome.steps_taken,
            durable_root_status="cancelled",
        )
    if isinstance(outcome, SafeYieldOutcome):
        return None
    if isinstance(outcome, BlockedOutcome):
        return None
    raise TypeError("unsupported segment outcome")
