"""Provider-neutral result and artifact reference models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from agentkit._types import JsonDict

ArtifactKind = Literal[
    "diff",
    "patch",
    "archive",
    "log",
    "branch",
    "pull_request",
    "url",
]
ResultKind = Literal[
    "turn_result",
    "session_result",
    "verification_summary",
    "failure_summary",
]


@dataclass(frozen=True)
class VerificationSummary:
    """Concise evidence summary for a turn or session result."""

    summary: str
    tool_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class FailureSummary:
    """Host-supplied failure details attached to a result."""

    message: str
    details: str | None = None
    retryable: bool | None = None
    metadata: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class ArtifactRef:
    """Reference to a produced artifact, without prescribing storage."""

    artifact_id: str
    kind: ArtifactKind
    title: str | None = None
    summary: str | None = None
    uri: str | None = None
    metadata: JsonDict = field(default_factory=dict)
    producer_turn_id: str | None = None


@dataclass(frozen=True)
class ResultRef:
    """Reference to a logical result that may link related artifacts/results."""

    result_id: str
    kind: ResultKind
    session_id: str | None = None
    turn_id: str | None = None
    created_at: str | None = None
    label: str | None = None
    summary: str | None = None
    artifact_ids: tuple[str, ...] = ()
    result_ids: tuple[str, ...] = ()
    metadata: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class TurnResult:
    """Provider-neutral summary of one agent turn."""

    final_output: str | None
    verification_summary: VerificationSummary | None = None
    artifact_refs: tuple[ArtifactRef, ...] = ()
    result_refs: tuple[ResultRef, ...] = ()
    metadata: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class SessionResult:
    """Provider-neutral summary of an agent session."""

    session_id: str
    status: str
    turn_id: str | None = None
    turn_status: str | None = None
    final_output: str | None = None
    verification_summary: VerificationSummary | None = None
    failure_summary: FailureSummary | None = None
    artifact_refs: tuple[ArtifactRef, ...] = ()
    result_refs: tuple[ResultRef, ...] = ()
    metadata: JsonDict = field(default_factory=dict)
