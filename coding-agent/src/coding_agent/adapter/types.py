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
