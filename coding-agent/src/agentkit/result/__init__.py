"""Provider-neutral agent result models and reducers."""

from agentkit.result.models import (
    ArtifactRef,
    FailureSummary,
    ResultRef,
    SessionResult,
    TurnResult,
    VerificationSummary,
)
from agentkit.result.reducers import (
    latest_turn_result,
    result_from_turn_trace,
    verification_summary_from_turn_trace,
)

__all__ = [
    "ArtifactRef",
    "FailureSummary",
    "ResultRef",
    "SessionResult",
    "TurnResult",
    "VerificationSummary",
    "latest_turn_result",
    "result_from_turn_trace",
    "verification_summary_from_turn_trace",
]
