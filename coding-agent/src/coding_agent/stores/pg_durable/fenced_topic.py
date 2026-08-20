"""Fenced PostgreSQL topic store wrapper."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from agentkit.storage.pg import (
    PGPool,
)
from coding_agent.topics.store import (
    PGTopicStore,
    TopicAnchorRecord,
    TopicCostRecord,
    TopicRecallLinkRecord,
    TopicRecord,
    TopicStatus,
)
from coding_agent.stores.runtime_store import (
    JSONObject,
)
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
)
from coding_agent.stores.pg_durable.store import PGDurableStore


class FencedPGTopicStore(PGTopicStore):
    def __init__(
        self,
        *,
        durable_store: PGDurableStore,
        pool: PGPool,
        authority_for_session: Callable[[str], OwnerAuthority],
    ) -> None:
        self._durable_store = durable_store
        self._delegate = PGTopicStore(pool=pool)
        self._authority_for_session = authority_for_session

    async def create_topic(self, record: TopicRecord) -> TopicRecord:
        return await self._durable_store.create_topic(
            self._authority_for_session(record.session_id),
            record,
        )

    async def finalize_topic(
        self,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int,
        finalized_at: datetime,
        metadata: JSONObject,
    ) -> TopicRecord:
        session_id = await self._require_session_id_for_topic(topic_id)
        return await self._durable_store.finalize_topic(
            self._authority_for_session(session_id),
            topic_id,
            summary=summary,
            topic_finalized_seq=topic_finalized_seq,
            finalized_at=finalized_at,
            metadata=metadata,
        )

    async def abort_topic(
        self,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int | None,
        finalized_at: datetime,
        metadata: JSONObject,
    ) -> TopicRecord:
        session_id = await self._require_session_id_for_topic(topic_id)
        return await self._durable_store.abort_topic(
            self._authority_for_session(session_id),
            topic_id,
            summary=summary,
            topic_finalized_seq=topic_finalized_seq,
            finalized_at=finalized_at,
            metadata=metadata,
        )

    async def delete_topic(self, topic_id: str) -> None:
        session_id = await self._require_session_id_for_topic(topic_id)
        await self._durable_store.delete_topic(
            self._authority_for_session(session_id),
            topic_id,
        )

    async def load_topic(self, topic_id: str) -> TopicRecord | None:
        return await self._delegate.load_topic(topic_id)

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
    ) -> list[TopicRecord]:
        return await self._delegate.list_topics(
            session_id=session_id,
            tape_id=tape_id,
            status=status,
            after_created_at=after_created_at,
            after_topic_id=after_topic_id,
            limit=limit,
            offset=offset,
        )

    async def find_open_topic(
        self,
        *,
        session_id: str,
        tape_id: str,
    ) -> TopicRecord | None:
        return await self._delegate.find_open_topic(
            session_id=session_id,
            tape_id=tape_id,
        )

    async def record_topic_anchor(
        self,
        record: TopicAnchorRecord,
    ) -> TopicAnchorRecord:
        session_id = await self._require_session_id_for_topic(record.topic_id)
        return await self._durable_store.record_topic_anchor(
            self._authority_for_session(session_id),
            record,
        )

    async def list_topic_anchors(self, topic_id: str) -> list[TopicAnchorRecord]:
        return await self._delegate.list_topic_anchors(topic_id)

    async def record_recall_link(
        self,
        record: TopicRecallLinkRecord,
    ) -> TopicRecallLinkRecord:
        session_id = await self._require_session_id_for_topic(record.source_topic_id)
        return await self._durable_store.record_recall_link(
            self._authority_for_session(session_id),
            record,
        )

    async def list_recall_links(
        self,
        source_topic_id: str,
    ) -> list[TopicRecallLinkRecord]:
        return await self._delegate.list_recall_links(source_topic_id)

    async def update_topic_cost(
        self,
        delta: TopicCostRecord,
    ) -> TopicCostRecord:
        session_id = await self._require_session_id_for_topic(delta.topic_id)
        return await self._durable_store.update_topic_cost(
            self._authority_for_session(session_id),
            delta,
        )

    async def load_topic_cost(self, topic_id: str) -> TopicCostRecord | None:
        return await self._delegate.load_topic_cost(topic_id)

    async def _require_session_id_for_topic(self, topic_id: str) -> str:
        session_id = await self._durable_store.session_id_for_topic(topic_id)
        if session_id is None:
            raise KeyError(f"topic not found: {topic_id}")
        return session_id
