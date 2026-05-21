"""Deterministic topic recall and context-pack helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentkit.tape.anchor import Anchor
from agentkit.tape.tape import Tape
from coding_agent.context_pack import (
    ContextPack,
    ContextPackItem,
    ContextPackRenderer,
    ContextPackSection,
    EvidenceRef,
)
from coding_agent.topic_lifecycle import _append_anchor, _remove_anchor
from coding_agent.topic_store import JSONObject, TopicRecallLinkRecord, TopicRecord

RECALL_ANCHOR = "recall_anchor"


class TopicRecallStore(Protocol):
    async def record_recall_link(
        self,
        record: TopicRecallLinkRecord,
    ) -> TopicRecallLinkRecord: ...


@dataclass(frozen=True)
class RecalledTopic:
    topic: TopicRecord
    score: float
    reason: str


def recall_topic_summaries(
    *,
    source_topic: TopicRecord,
    candidates: list[TopicRecord],
    query: str | None = None,
    limit: int = 5,
) -> list[RecalledTopic]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    query_tokens = _tokens(query or _topic_text(source_topic))
    if not query_tokens:
        return []

    recalled: list[RecalledTopic] = []
    for candidate in candidates:
        if candidate.topic_id == source_topic.topic_id:
            continue
        if candidate.status != "finalized" or candidate.summary is None:
            continue
        candidate_tokens = _tokens(_topic_text(candidate))
        overlap = query_tokens & candidate_tokens
        if not overlap:
            continue
        score = round(len(overlap) / len(query_tokens), 4)
        recalled.append(
            RecalledTopic(
                topic=candidate,
                score=score,
                reason="deterministic_token_overlap",
            )
        )
    recalled.sort(
        key=lambda item: (
            -item.score,
            item.topic.created_at,
            item.topic.topic_id,
        )
    )
    return recalled[:limit]


async def record_topic_recall(
    *,
    tape: Tape,
    store: TopicRecallStore,
    source_topic: TopicRecord,
    recalled: RecalledTopic,
    relation: str = "summary_recall",
    metadata: JSONObject | None = None,
) -> TopicRecallLinkRecord:
    if tape.tape_id != source_topic.tape_id:
        raise ValueError(
            f"source topic {source_topic.topic_id} belongs to tape "
            f"{source_topic.tape_id}"
        )
    anchor = Anchor(
        anchor_type="context",
        payload={"label": "Topic recall"},
        meta={
            "topic_id": source_topic.topic_id,
            "recalled_topic_id": recalled.topic.topic_id,
            "product_anchor_type": RECALL_ANCHOR,
            "skip": True,
        },
    )
    seq = _append_anchor(tape, anchor)
    source_entry_start_seq = None
    source_entry_end_seq = None
    if seq >= source_topic.topic_initial_seq:
        source_entry_start_seq = source_topic.topic_initial_seq
        source_entry_end_seq = seq
    try:
        return await store.record_recall_link(
            TopicRecallLinkRecord(
                source_topic_id=source_topic.topic_id,
                recalled_topic_id=recalled.topic.topic_id,
                relation=relation,
                anchor_seq=seq,
                source_entry_start_seq=source_entry_start_seq,
                source_entry_end_seq=source_entry_end_seq,
                metadata={
                    "reason": recalled.reason,
                    "score_bucket": _score_bucket(recalled.score),
                    **dict(metadata or {}),
                },
            )
        )
    except Exception:
        _remove_anchor(tape, seq=seq, entry_id=anchor.id)
        raise


def topic_recall_context_pack(
    recalled_topics: list[RecalledTopic],
    *,
    enabled: bool,
) -> ContextPack:
    if not enabled or not recalled_topics:
        return ContextPack(sections=())
    items = tuple(_context_item(recalled) for recalled in recalled_topics)
    return ContextPack(
        sections=(
            ContextPackSection(
                title="Recalled topic references",
                items=items,
            ),
        )
    )


def topic_recall_context_messages(
    recalled_topics: list[RecalledTopic],
    *,
    enabled: bool,
) -> list[dict[str, object]]:
    return ContextPackRenderer().render_messages(
        topic_recall_context_pack(recalled_topics, enabled=enabled)
    )


def _context_item(recalled: RecalledTopic) -> ContextPackItem:
    topic = recalled.topic
    source_range = {
        "topic_id": topic.topic_id,
        "start_seq": topic.topic_initial_seq,
    }
    if topic.topic_finalized_seq is not None:
        source_range["end_seq"] = topic.topic_finalized_seq
    return ContextPackItem(
        source_kind="topic_summary",
        source_id=f"topic:{topic.topic_id}",
        label=topic.title or topic.topic_id,
        body=topic.summary,
        score=recalled.score,
        evidence=(
            EvidenceRef(
                kind="topic",
                source_id=topic.topic_id,
                label="topic summary",
                session_id=topic.session_id,
            ),
        ),
        metadata={
            "source_topic_ids": [topic.topic_id],
            "source_entry_ranges": [source_range],
            "reason": recalled.reason,
        },
    )


def _topic_text(topic: TopicRecord) -> str:
    return " ".join(part for part in (topic.title or "", topic.summary or "") if part)


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in (
            "".join(char.lower() if char.isalnum() else " " for char in value)
        ).split()
        if len(token) >= 3
    }


def _score_bucket(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"
