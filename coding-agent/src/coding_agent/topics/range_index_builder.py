"""Build deterministic topic range indexes from durable topic stores."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from inspect import isawaitable
from typing import Protocol, cast

from coding_agent.topics.range_index import TopicRangeIndex
from coding_agent.topics.store import TopicRecord, TopicStatus

logger = logging.getLogger(__name__)


class TopicRangeListingStore(Protocol):
    async def list_topics(
        self,
        *,
        session_id: str | None = None,
        tape_id: str | None = None,
        status: TopicStatus | None = None,
        after_created_at: datetime | None = None,
        after_topic_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[TopicRecord]: ...


@dataclass(frozen=True)
class TopicRangeIndexBuildResult:
    index: TopicRangeIndex
    topic_count: int
    skipped_count: int = 0


async def build_topic_range_index_from_store(
    topic_store: object | None,
    *,
    max_topics: int = 10_000,
) -> TopicRangeIndexBuildResult | None:
    """Derive a deterministic recall index from finalized durable topics."""

    if topic_store is None:
        return None
    if (
        isinstance(max_topics, bool)
        or not isinstance(max_topics, int)
        or max_topics <= 0
    ):
        raise ValueError("max_topics must be positive")

    list_topics = getattr(topic_store, "list_topics", None)
    if not callable(list_topics):
        return None

    result = list_topics(status="finalized", limit=max_topics + 1)
    if not isawaitable(result):
        raise TypeError("topic_store.list_topics must be async")
    topics = tuple(cast(Sequence[TopicRecord], await result))
    if len(topics) > max_topics:
        raise RuntimeError("too many finalized topics to build semantic topic index")

    index = TopicRangeIndex()
    skipped_count = 0
    for topic in topics:
        try:
            index.index_topic(topic)
        except ValueError as exc:
            skipped_count += 1
            logger.warning(
                "Skipping recall-unsafe finalized topic while building semantic topic index",
                extra={
                    "topic_id": topic.topic_id,
                    "session_id": topic.session_id,
                    "reason": str(exc),
                },
            )
    return TopicRangeIndexBuildResult(
        index=index,
        topic_count=len(topics),
        skipped_count=skipped_count,
    )
