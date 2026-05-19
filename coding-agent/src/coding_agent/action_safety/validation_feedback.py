from __future__ import annotations

import hashlib
from coding_agent.context_pack import (
    ContextPack,
    ContextPackItem,
    ContextPackRenderer,
    ContextPackSection,
    EvidenceRef,
)

from .validation_runner import ValidationOutcome, ValidationReport, ValidationStatus


_RENDERER = ContextPackRenderer(max_item_chars=500)
_INT_SUMMARY_KEYS = frozenset(
    {
        "timeout_seconds",
        "stdout_bytes",
        "stderr_bytes",
        "stdout_lines",
        "stderr_lines",
    }
)
_LABEL_SUMMARY_KEYS = frozenset({"policy_decision", "error_kind"})
_SAFE_SUMMARY_LABEL_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-"
)


def validation_feedback_context_pack(report: ValidationReport) -> ContextPack:
    items = tuple(
        _context_item_from_outcome(outcome, index=index)
        for index, outcome in enumerate(report.outcomes, start=1)
        if outcome.status != ValidationStatus.PASSED
    )
    if not items:
        return ContextPack(sections=())
    return ContextPack(
        sections=(
            ContextPackSection(
                title="Validation feedback",
                items=items,
            ),
        )
    )


def render_validation_feedback_messages(
    report: ValidationReport,
) -> list[dict[str, object]]:
    return _RENDERER.render_messages(validation_feedback_context_pack(report))


def _context_item_from_outcome(
    outcome: ValidationOutcome,
    *,
    index: int,
) -> ContextPackItem:
    source_id = _source_id(outcome, index=index)
    return ContextPackItem(
        source_kind="runtime_hint",
        source_id=source_id,
        label=f"Validation outcome {index}: {outcome.status.value}",
        body=_failure_body(outcome),
        evidence=(
            EvidenceRef(
                kind="validation",
                source_id=source_id,
                label="validation outcome",
            ),
        ),
        metadata={
            "status": outcome.status.value,
            "exit_code": outcome.exit_code,
            "duration_ms": outcome.duration_ms,
            "policy_decision": outcome.policy.decision.value,
            "policy_reason_count": len(outcome.policy.reasons),
        },
    )


def _failure_body(outcome: ValidationOutcome) -> str:
    parts = [
        f"Status: {outcome.status.value}.",
        f"Policy: {outcome.policy.decision.value}.",
        f"Duration: {outcome.duration_ms} ms.",
    ]
    if outcome.exit_code is not None:
        parts.append(f"Exit code: {outcome.exit_code}.")
    if outcome.failure_summary:
        summary = _bounded_summary(outcome)
        if summary:
            parts.append(f"Failure metadata: {summary}.")
    return " ".join(parts)


def _bounded_summary(outcome: ValidationOutcome) -> str:
    parts: list[str] = []
    for key in sorted(_INT_SUMMARY_KEYS):
        value = outcome.failure_summary.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            parts.append(f"{key}={value}")
    for key in sorted(_LABEL_SUMMARY_KEYS):
        value = outcome.failure_summary.get(key)
        if isinstance(value, str) and _safe_summary_label(value):
            parts.append(f"{key}={value}")
    return ", ".join(parts)


def _source_id(outcome: ValidationOutcome, *, index: int) -> str:
    payload = (
        f"{index}:{outcome.status.value}:"
        f"{outcome.exit_code}:{outcome.policy.decision.value}"
    )
    return hashlib.sha256(f"validation:{payload}".encode("utf-8")).hexdigest()


def _safe_summary_label(value: str) -> bool:
    return 0 < len(value) <= 80 and all(
        char in _SAFE_SUMMARY_LABEL_CHARS for char in value
    )
