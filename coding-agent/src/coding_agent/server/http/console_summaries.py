"""Console tape, context, memory, action, and correlation summaries."""

from __future__ import annotations

import logging
from collections.abc import Iterable


from coding_agent.stores.runtime_store import (
    AgentRunRecord,
)
from coding_agent.server.developer_console import (
    ConsoleActionValidationSummary,
    ConsoleContextEvidence,
    ConsoleContextSectionSummary,
    ConsoleContextSummary,
    ConsoleCorrelationSummary,
    ConsoleMemoryEvidence,
    ConsoleMemoryReviewSummary,
    ConsoleMemorySummary,
    ConsoleTapeEntrySummary,
    safe_id_value,
    safe_key_tuple,
    safe_label_value,
    safe_text_value,
)

from coding_agent.server.http._bindings import LOGGER_NAME

from coding_agent.server.http.console_actions import (
    _action_summary_from_item,
    _first_metadata_item,
    _metadata_lists,
    _optional_int,
    _validation_outcomes,
)
from coding_agent.server.http.console_run_meta import _topic_id_from_run
from coding_agent.server.http.deps import _safe_dict

logger = logging.getLogger(LOGGER_NAME)


def _tape_entry_summary(result: object) -> ConsoleTapeEntrySummary:
    entry = _safe_dict(getattr(result, "entry", {}))
    payload = _safe_dict(entry.get("payload"))
    meta = _safe_dict(entry.get("meta"))
    kind = entry.get("kind")
    return ConsoleTapeEntrySummary(
        tape_id=str(getattr(result, "tape_id", "")),
        seq=int(getattr(result, "seq", 0)),
        kind=kind if isinstance(kind, str) else "-",
        run_id=safe_id_value(payload.get("run_id") or meta.get("run_id")),
        tool_call_id=safe_id_value(
            payload.get("tool_call_id") or meta.get("tool_call_id")
        ),
        anchor_type=safe_id_value(meta.get("anchor_type")),
        payload_keys=safe_key_tuple(payload),
        meta_keys=safe_key_tuple(meta),
    )


def _context_summary_from_run(run: AgentRunRecord) -> ConsoleContextSummary | None:
    raw_pack = run.metadata.get("context_pack")
    if not isinstance(raw_pack, dict):
        return ConsoleContextSummary(run_id=run.run_id, sections=())
    raw_sections = raw_pack.get("sections")
    if not isinstance(raw_sections, list):
        return ConsoleContextSummary(run_id=run.run_id, sections=())
    sections: list[ConsoleContextSectionSummary] = []
    for raw_section in raw_sections:
        if not isinstance(raw_section, dict):
            continue
        title = safe_text_value(raw_section.get("title")) or "Context"
        raw_items = raw_section.get("items")
        if not isinstance(raw_items, list):
            continue
        items = tuple(
            item
            for raw_item in raw_items
            if isinstance(raw_item, dict)
            for item in [_context_evidence_from_item(raw_item)]
            if item is not None
        )
        if items:
            sections.append(ConsoleContextSectionSummary(title=title, items=items))
    return ConsoleContextSummary(run_id=run.run_id, sections=tuple(sections))


def _context_evidence_from_item(
    raw_item: dict[str, object],
) -> ConsoleContextEvidence | None:
    source_id = safe_text_value(raw_item.get("source_id"))
    label = safe_text_value(raw_item.get("label"))
    source_kind = safe_text_value(raw_item.get("source_kind"))
    if source_id is None or label is None or source_kind is None:
        return None
    evidence_reason = None
    raw_evidence = raw_item.get("evidence")
    if isinstance(raw_evidence, list) and raw_evidence:
        first = raw_evidence[0]
        if isinstance(first, dict):
            evidence_reason = safe_text_value(first.get("label"))
    score_raw = raw_item.get("score")
    score = float(score_raw) if isinstance(score_raw, int | float) else None
    return ConsoleContextEvidence(
        kind=source_kind,
        label=label,
        source_id=source_id,
        repo_path=safe_text_value(raw_item.get("repo_path")),
        line_start=_optional_int(raw_item.get("line_start")),
        line_end=_optional_int(raw_item.get("line_end")),
        score=score,
        score_scale=safe_label_value(raw_item.get("score_scale")),
        reason=evidence_reason,
    )


def _memory_summary_from_runs(runs: Iterable[AgentRunRecord]) -> ConsoleMemorySummary:
    items: list[ConsoleMemoryEvidence] = []
    reviews: list[ConsoleMemoryReviewSummary] = []
    seen_items: set[tuple[str | None, str]] = set()
    seen_reviews: set[tuple[str | None, str]] = set()
    for run in runs:
        summary = _memory_summary_from_run(run)
        for item in summary.items:
            key = (item.run_id, item.source_id)
            if key in seen_items:
                continue
            seen_items.add(key)
            items.append(item)
        for review in summary.reviews:
            key = (review.run_id, review.source_id)
            if key in seen_reviews:
                continue
            seen_reviews.add(key)
            reviews.append(review)
    return ConsoleMemorySummary(
        run_id=None,
        items=tuple(items),
        reviews=tuple(reviews),
    )


def _memory_summary_from_run(run: AgentRunRecord) -> ConsoleMemorySummary:
    items: list[ConsoleMemoryEvidence] = []
    reviews: list[ConsoleMemoryReviewSummary] = []
    seen_source_ids: set[str] = set()
    context = _context_summary_from_run(run)
    if context is not None:
        for section in context.sections:
            for item in section.items:
                if item.kind != "memory":
                    continue
                if item.source_id in seen_source_ids:
                    continue
                seen_source_ids.add(item.source_id)
                items.append(
                    ConsoleMemoryEvidence(
                        run_id=run.run_id,
                        source_id=item.source_id,
                        label="Memory",
                        status="context_pack",
                        tags_count=None,
                        evidence_count=None,
                        repo_path=item.repo_path,
                        line_start=item.line_start,
                        line_end=item.line_end,
                    )
                )
    for raw_item in _metadata_lists(
        run.metadata,
        ("memory_evidence", "memory_candidates", "memories"),
    ):
        review = _memory_review_from_item(run.run_id, raw_item)
        if review is not None:
            reviews.append(review)
        memory = _memory_evidence_from_item(run.run_id, raw_item)
        if memory is None or memory.source_id in seen_source_ids:
            continue
        seen_source_ids.add(memory.source_id)
        items.append(memory)
    return ConsoleMemorySummary(
        run_id=run.run_id,
        items=tuple(items),
        reviews=tuple(
            sorted(
                reviews,
                key=lambda item: (
                    item.status,
                    item.kind,
                    item.source_id,
                ),
            )
        ),
    )


def _memory_evidence_from_item(
    run_id: str,
    raw_item: dict[str, object],
) -> ConsoleMemoryEvidence | None:
    source_id = (
        safe_id_value(raw_item.get("source_id"))
        or safe_id_value(raw_item.get("memory_id"))
        or safe_id_value(raw_item.get("id"))
    )
    if source_id is None:
        return None
    label = safe_text_value(raw_item.get("label")) or "Memory"
    evidence = raw_item.get("evidence")
    tags = raw_item.get("tags")
    return ConsoleMemoryEvidence(
        run_id=run_id,
        source_id=source_id,
        label=label,
        status=safe_label_value(raw_item.get("status")),
        tags_count=len(tags) if isinstance(tags, list) else None,
        evidence_count=len(evidence) if isinstance(evidence, list) else None,
        repo_path=safe_text_value(raw_item.get("repo_path")),
        line_start=_optional_int(raw_item.get("line_start")),
        line_end=_optional_int(raw_item.get("line_end")),
    )


def _memory_review_from_item(
    run_id: str,
    raw_item: dict[str, object],
) -> ConsoleMemoryReviewSummary | None:
    source_id = (
        safe_id_value(raw_item.get("candidate_id"))
        or safe_id_value(raw_item.get("source_id"))
        or safe_id_value(raw_item.get("memory_id"))
        or safe_id_value(raw_item.get("id"))
    )
    if source_id is None:
        return None
    status = safe_label_value(raw_item.get("status")) or "candidate"
    if status not in {"candidate", "accepted", "rejected", "archived"}:
        status = "redacted"
    provenance = _safe_dict(raw_item.get("provenance"))
    source_ranges = provenance.get("source_entry_ranges")
    evidence_refs = provenance.get("evidence_refs") or raw_item.get("evidence")
    return ConsoleMemoryReviewSummary(
        source_id=source_id,
        label=safe_text_value(raw_item.get("title") or raw_item.get("label"))
        or "Memory",
        kind=safe_label_value(raw_item.get("kind")) or "unknown",
        status=status,
        run_id=safe_id_value(run_id),
        topic_id=safe_id_value(provenance.get("topic_id") or raw_item.get("topic_id")),
        task_id=safe_id_value(provenance.get("task_id") or raw_item.get("task_id")),
        evidence_count=len(evidence_refs) if isinstance(evidence_refs, list) else None,
        source_range_count=len(source_ranges)
        if isinstance(source_ranges, list)
        else None,
    )


def _action_validation_summary_from_run(
    run: AgentRunRecord,
) -> ConsoleActionValidationSummary:
    actions = tuple(
        action
        for raw_item in _metadata_lists(run.metadata, ("actions", "action_summaries"))
        for action in [_action_summary_from_item(run.run_id, raw_item)]
        if action is not None
    )
    validation_report = _safe_dict(
        run.metadata.get("validation_report") or run.metadata.get("validation")
    )
    validations = tuple(_validation_outcomes(validation_report))
    validation_status = safe_label_value(validation_report.get("status"))
    return ConsoleActionValidationSummary(
        run_id=run.run_id,
        actions=actions,
        validation_status=validation_status,
        validations=validations,
    )


def _correlation_summary_from_run(run: AgentRunRecord) -> ConsoleCorrelationSummary:
    action = _first_metadata_item(run.metadata, ("actions", "action_summaries"))
    return ConsoleCorrelationSummary(
        session_id=safe_id_value(run.session_id),
        run_id=safe_id_value(run.run_id),
        tape_id=safe_id_value(run.tape_id),
        topic_id=_topic_id_from_run(run),
        retrieval_id=safe_id_value(
            run.metadata.get("retrieval_id") or run.metadata.get("context_retrieval_id")
        ),
        action_id=(
            safe_id_value(action.get("action_id") or action.get("id"))
            if action is not None
            else safe_id_value(run.metadata.get("action_id"))
        ),
        validation_id=(
            safe_id_value(action.get("validation_id"))
            if action is not None
            else safe_id_value(run.metadata.get("validation_id"))
        ),
        interaction_id=(
            safe_id_value(
                action.get("interaction_id") or action.get("approval_interaction_id")
            )
            if action is not None
            else safe_id_value(run.metadata.get("interaction_id"))
        ),
    )


__all__ = [
    "_action_validation_summary_from_run",
    "_context_evidence_from_item",
    "_context_summary_from_run",
    "_correlation_summary_from_run",
    "_memory_evidence_from_item",
    "_memory_review_from_item",
    "_memory_summary_from_run",
    "_memory_summary_from_runs",
    "_tape_entry_summary",
]
