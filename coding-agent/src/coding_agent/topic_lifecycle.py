"""Topic lifecycle helpers that bind durable Topic records to tape anchors."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from agentkit.tape.anchor import Anchor
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from coding_agent.topic_memory import (
    MemoryReviewStore,
    propose_memory_candidate_from_topic,
)
from coding_agent.topic_store import (
    JSONObject,
    TopicAnchorRecord,
    TopicRecord,
)

TOPIC_INITIAL = "topic_initial"
TOPIC_FINALIZED = "topic_finalized"
TOPIC_ABORTED = "topic_aborted"


class TopicLifecycleStore(Protocol):
    async def create_topic(self, record: TopicRecord) -> TopicRecord: ...

    async def finalize_topic(
        self,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int,
        finalized_at: datetime,
        metadata: JSONObject,
    ) -> TopicRecord: ...

    async def abort_topic(
        self,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int | None,
        finalized_at: datetime,
        metadata: JSONObject,
    ) -> TopicRecord: ...

    async def record_topic_anchor(
        self,
        record: TopicAnchorRecord,
    ) -> TopicAnchorRecord: ...


@dataclass(frozen=True)
class TopicAnchorView:
    seq: int
    entry: Anchor
    topic_id: str | None
    product_anchor_type: str | None


class TopicLifecycle:
    def __init__(
        self,
        *,
        store: TopicLifecycleStore,
        now: Callable[[], datetime] | None = None,
        topic_id_factory: Callable[[], str] | None = None,
        memory_review_store: MemoryReviewStore | None = None,
    ) -> None:
        self._store = store
        self._now = now or (lambda: datetime.now(UTC))
        self._topic_id_factory = topic_id_factory or _new_topic_id
        self._memory_review_store = memory_review_store

    async def create_topic(
        self,
        *,
        tape: Tape,
        session_id: str,
        kind: str,
        title: str | None = None,
        owner: str | None = None,
        metadata: JSONObject | None = None,
    ) -> TopicRecord:
        topic_id = self._topic_id_factory()
        created_at = self._now()
        anchor = _topic_anchor(
            topic_id=topic_id,
            product_anchor_type=TOPIC_INITIAL,
            encoded_anchor_type="topic_start",
            label=title or "Topic started",
        )
        seq = _append_anchor(tape, anchor)
        record = TopicRecord(
            topic_id=topic_id,
            tape_id=tape.tape_id,
            session_id=session_id,
            kind=kind,
            status="open",
            title=title,
            summary=None,
            owner=owner,
            topic_initial_seq=seq,
            topic_finalized_seq=None,
            created_at=created_at,
            finalized_at=None,
            metadata=dict(metadata or {}),
        )
        try:
            stored = await self._store.create_topic(record)
            await self._record_anchor(
                stored,
                seq=seq,
                product_anchor_type=TOPIC_INITIAL,
                entry_id=anchor.id,
                encoded_anchor_type="topic_start",
            )
        except Exception:
            _remove_anchor(tape, seq=seq, entry_id=anchor.id)
            raise
        return stored

    async def finalize_topic(
        self,
        *,
        tape: Tape,
        topic: TopicRecord,
        summary: str | None,
        metadata: JSONObject | None = None,
    ) -> TopicRecord:
        return await self._close_topic(
            tape=tape,
            topic=topic,
            summary=summary,
            metadata=dict(metadata or {}),
            product_anchor_type=TOPIC_FINALIZED,
            close_status="finalized",
        )

    async def abort_topic(
        self,
        *,
        tape: Tape,
        topic: TopicRecord,
        summary: str | None,
        metadata: JSONObject | None = None,
    ) -> TopicRecord:
        return await self._close_topic(
            tape=tape,
            topic=topic,
            summary=summary,
            metadata=dict(metadata or {}),
            product_anchor_type=TOPIC_ABORTED,
            close_status="aborted",
        )

    async def _close_topic(
        self,
        *,
        tape: Tape,
        topic: TopicRecord,
        summary: str | None,
        metadata: JSONObject,
        product_anchor_type: str,
        close_status: str,
    ) -> TopicRecord:
        _require_matching_tape(tape, topic)
        finalized_at = self._now()
        anchor = _topic_anchor(
            topic_id=topic.topic_id,
            product_anchor_type=product_anchor_type,
            encoded_anchor_type="topic_end",
            label=summary or f"Topic {close_status}",
        )
        seq = _append_anchor(tape, anchor)
        try:
            if close_status == "finalized":
                stored = await self._store.finalize_topic(
                    topic.topic_id,
                    summary=summary,
                    topic_finalized_seq=seq,
                    finalized_at=finalized_at,
                    metadata=metadata,
                )
            elif close_status == "aborted":
                stored = await self._store.abort_topic(
                    topic.topic_id,
                    summary=summary,
                    topic_finalized_seq=seq,
                    finalized_at=finalized_at,
                    metadata=metadata,
                )
            else:
                raise ValueError(f"unsupported close_status: {close_status}")
            await self._record_anchor(
                stored,
                seq=seq,
                product_anchor_type=product_anchor_type,
                entry_id=anchor.id,
                encoded_anchor_type="topic_end",
            )
        except Exception:
            _remove_anchor(tape, seq=seq, entry_id=anchor.id)
            raise
        if close_status == "finalized" and self._memory_review_store is not None:
            candidate = propose_memory_candidate_from_topic(stored)
            if candidate is not None:
                self._memory_review_store.add_candidate(candidate)
        return stored

    async def _record_anchor(
        self,
        topic: TopicRecord,
        *,
        seq: int,
        product_anchor_type: str,
        entry_id: str,
        encoded_anchor_type: str,
    ) -> TopicAnchorRecord:
        return await self._store.record_topic_anchor(
            TopicAnchorRecord(
                topic_id=topic.topic_id,
                tape_id=topic.tape_id,
                seq=seq,
                anchor_type=product_anchor_type,
                entry_id=entry_id,
                metadata={
                    "encoded_anchor_type": encoded_anchor_type,
                    "product_anchor_type": product_anchor_type,
                },
            )
        )


def topic_range_entries(
    tape: Tape,
    topic: TopicRecord,
    *,
    include_anchors: bool = True,
) -> list[Entry]:
    _require_matching_tape(tape, topic)
    end_seq = topic.topic_finalized_seq
    entries = list(tape)
    if end_seq is None:
        end_seq = len(entries) - 1
    if end_seq >= len(entries):
        raise ValueError("topic range exceeds tape length")
    ranged = entries[topic.topic_initial_seq : end_seq + 1]
    if include_anchors:
        return ranged
    return [entry for entry in ranged if entry.kind != "anchor"]


def find_topic_anchors(
    tape: Tape,
    *,
    product_anchor_type: str | None = None,
) -> list[TopicAnchorView]:
    anchors: list[TopicAnchorView] = []
    for seq, entry in enumerate(tape):
        if not isinstance(entry, Anchor):
            continue
        raw_product_anchor_type = entry.meta.get("product_anchor_type")
        if not isinstance(raw_product_anchor_type, str):
            raw_product_anchor_type = None
        if raw_product_anchor_type is None:
            continue
        if (
            product_anchor_type is not None
            and raw_product_anchor_type != product_anchor_type
        ):
            continue
        topic_id = entry.meta.get("topic_id")
        anchors.append(
            TopicAnchorView(
                seq=seq,
                entry=entry,
                topic_id=topic_id if isinstance(topic_id, str) else None,
                product_anchor_type=raw_product_anchor_type,
            )
        )
    return anchors


def _topic_anchor(
    *,
    topic_id: str,
    product_anchor_type: str,
    encoded_anchor_type: str,
    label: str,
) -> Anchor:
    return Anchor(
        anchor_type=encoded_anchor_type,
        payload={"label": label},
        meta={
            "topic_id": topic_id,
            "product_anchor_type": product_anchor_type,
            "skip": True,
        },
    )


def _new_topic_id() -> str:
    return f"topic-{uuid.uuid4().hex}"


def _append_anchor(tape: Tape, anchor: Anchor) -> int:
    with tape._lock:
        seq = len(tape._entries)
        tape._entries.append(anchor)
        return seq


def _remove_anchor(tape: Tape, *, seq: int, entry_id: str) -> None:
    with tape._lock:
        if seq >= len(tape._entries):
            return
        entry = tape._entries[seq]
        if entry.id == entry_id:
            del tape._entries[seq]


def _require_matching_tape(tape: Tape, topic: TopicRecord) -> None:
    if tape.tape_id != topic.tape_id:
        raise ValueError(f"topic {topic.topic_id} belongs to tape {topic.tape_id}")
