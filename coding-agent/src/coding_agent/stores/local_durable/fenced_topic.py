"""Fenced SQLite topic store wrapper."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from coding_agent.topics.store import (
    SQLiteTopicStore,
    TopicAnchorRecord,
    TopicCostRecord,
    TopicRecallLinkRecord,
    TopicRecord,
)
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
)
from coding_agent.stores.local_durable.store import SQLiteLocalDurableStore


class FencedSQLiteTopicStore(SQLiteTopicStore):
    def __init__(
        self,
        *,
        durable_store: SQLiteLocalDurableStore,
        path: Path,
        authority_for_session: Callable[[str], OwnerAuthority],
    ) -> None:
        super().__init__(path)
        self._durable_store = durable_store
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
        metadata: dict[str, Any],
    ) -> TopicRecord:
        session_id = self._require_session_id_for_topic(topic_id)
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
        metadata: dict[str, Any],
    ) -> TopicRecord:
        session_id = self._require_session_id_for_topic(topic_id)
        return await self._durable_store.abort_topic(
            self._authority_for_session(session_id),
            topic_id,
            summary=summary,
            topic_finalized_seq=topic_finalized_seq,
            finalized_at=finalized_at,
            metadata=metadata,
        )

    async def delete_topic(self, topic_id: str) -> None:
        session_id = self._require_session_id_for_topic(topic_id)
        await self._durable_store.delete_topic(
            self._authority_for_session(session_id),
            topic_id,
        )

    async def record_topic_anchor(
        self,
        record: TopicAnchorRecord,
    ) -> TopicAnchorRecord:
        session_id = self._require_session_id_for_topic(record.topic_id)
        return await self._durable_store.record_topic_anchor(
            self._authority_for_session(session_id),
            record,
        )

    async def record_recall_link(
        self,
        record: TopicRecallLinkRecord,
    ) -> TopicRecallLinkRecord:
        session_id = self._require_session_id_for_topic(record.source_topic_id)
        return await self._durable_store.record_recall_link(
            self._authority_for_session(session_id),
            record,
        )

    async def update_topic_cost(
        self,
        delta: TopicCostRecord,
    ) -> TopicCostRecord:
        session_id = self._require_session_id_for_topic(delta.topic_id)
        return await self._durable_store.update_topic_cost(
            self._authority_for_session(session_id),
            delta,
        )

    def _require_session_id_for_topic(self, topic_id: str) -> str:
        session_id = self._durable_store.session_id_for_topic(topic_id)
        if session_id is None:
            raise KeyError(f"topic not found: {topic_id}")
        return session_id
