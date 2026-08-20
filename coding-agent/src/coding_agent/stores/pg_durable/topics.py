"""Fenced PostgreSQL topic lifecycle writes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from coding_agent.topics.store import (
    PGTopicStore,
    TopicAnchorRecord,
    TopicCostRecord,
    TopicRecallLinkRecord,
    TopicRecord,
    _topic_anchor_from_row,
    _topic_cost_from_row,
    _topic_from_row,
    _topic_recall_link_from_row,
)
from coding_agent.stores.runtime_store import (
    JSONObject,
)
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SessionOwnershipConflictError,
)
from coding_agent.stores.pg_durable.helpers import (
    _required_row,
    _required_str,
)


class PgTopicsMixin:
    async def create_topic(
        self,
        authority: OwnerAuthority,
        record: TopicRecord,
    ) -> TopicRecord:
        if record.session_id != authority.session_id:
            raise SessionOwnershipConflictError("topic target belongs to another owner")

        async def body(connection: Any) -> TopicRecord:
            await self._require_owner(connection, authority)
            await self._require_stable_tape(connection, authority, record.tape_id)
            row = await connection.fetchrow(
                PGTopicStore._INSERT_TOPIC_SQL,
                record.topic_id,
                record.tape_id,
                record.session_id,
                record.kind,
                record.status,
                record.title,
                record.summary,
                record.owner,
                record.topic_initial_seq,
                record.topic_finalized_seq,
                record.created_at,
                record.finalized_at,
                record.metadata,
            )
            topic = _topic_from_row(_required_row(row, "topic insert"))
            if (
                topic.session_id != authority.session_id
                or topic.tape_id != record.tape_id
            ):
                raise SessionOwnershipConflictError(
                    "topic target belongs to another owner"
                )
            return topic

        return cast(TopicRecord, await self._with_transaction(body))

    async def finalize_topic(
        self,
        authority: OwnerAuthority,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int,
        finalized_at: datetime,
        metadata: JSONObject,
    ) -> TopicRecord:
        return await self._close_topic(
            authority,
            PGTopicStore._FINALIZE_TOPIC_SQL,
            "finalize",
            topic_id,
            summary=summary,
            topic_finalized_seq=topic_finalized_seq,
            finalized_at=finalized_at,
            metadata=metadata,
        )

    async def abort_topic(
        self,
        authority: OwnerAuthority,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int | None,
        finalized_at: datetime,
        metadata: JSONObject,
    ) -> TopicRecord:
        return await self._close_topic(
            authority,
            PGTopicStore._ABORT_TOPIC_SQL,
            "abort",
            topic_id,
            summary=summary,
            topic_finalized_seq=topic_finalized_seq,
            finalized_at=finalized_at,
            metadata=metadata,
        )

    async def _close_topic(
        self,
        authority: OwnerAuthority,
        query: str,
        operation: str,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int | None,
        finalized_at: datetime,
        metadata: JSONObject,
    ) -> TopicRecord:
        async def body(connection: Any) -> TopicRecord:
            await self._require_owner(connection, authority)
            _ = await self._lock_topic_targets(connection, authority, [topic_id])
            row = await connection.fetchrow(
                query,
                topic_id,
                summary,
                topic_finalized_seq,
                finalized_at,
                metadata,
            )
            if row is None:
                raise KeyError(f"open topic not found for {operation}: {topic_id}")
            return _topic_from_row(row)

        return cast(TopicRecord, await self._with_transaction(body))

    async def delete_topic(
        self,
        authority: OwnerAuthority,
        topic_id: str,
    ) -> None:
        async def body(connection: Any) -> None:
            await self._require_owner(connection, authority)
            _ = await self._lock_topic_targets(connection, authority, [topic_id])
            await connection.execute(
                PGTopicStore._DELETE_TOPIC_RECALL_LINKS_SQL,
                topic_id,
            )
            await connection.execute(PGTopicStore._DELETE_TOPIC_COST_SQL, topic_id)
            await connection.execute(PGTopicStore._DELETE_TOPIC_ANCHORS_SQL, topic_id)
            await connection.execute(PGTopicStore._DELETE_TOPIC_SQL, topic_id)

        await self._with_transaction(body)

    async def record_topic_anchor(
        self,
        authority: OwnerAuthority,
        record: TopicAnchorRecord,
    ) -> TopicAnchorRecord:
        async def body(connection: Any) -> TopicAnchorRecord:
            await self._require_owner(connection, authority)
            topic_tapes = await self._lock_topic_targets(
                connection,
                authority,
                [record.topic_id],
            )
            if record.tape_id != topic_tapes[record.topic_id]:
                raise SessionOwnershipConflictError(
                    "topic anchor target belongs to another tape"
                )
            row = await connection.fetchrow(
                PGTopicStore._INSERT_ANCHOR_SQL,
                record.topic_id,
                record.tape_id,
                record.seq,
                record.anchor_type,
                record.entry_id,
                record.metadata,
            )
            return _topic_anchor_from_row(_required_row(row, "topic anchor upsert"))

        return cast(TopicAnchorRecord, await self._with_transaction(body))

    async def record_recall_link(
        self,
        authority: OwnerAuthority,
        record: TopicRecallLinkRecord,
    ) -> TopicRecallLinkRecord:
        async def body(connection: Any) -> TopicRecallLinkRecord:
            await self._require_owner(connection, authority)
            _ = await self._lock_topic_targets(
                connection,
                authority,
                [record.source_topic_id, record.recalled_topic_id],
            )
            row = await connection.fetchrow(
                PGTopicStore._INSERT_RECALL_LINK_SQL,
                record.source_topic_id,
                record.recalled_topic_id,
                record.relation,
                record.anchor_seq,
                record.source_entry_start_seq,
                record.source_entry_end_seq,
                record.metadata,
            )
            return _topic_recall_link_from_row(
                _required_row(row, "topic recall link upsert")
            )

        return cast(TopicRecallLinkRecord, await self._with_transaction(body))

    async def update_topic_cost(
        self,
        authority: OwnerAuthority,
        delta: TopicCostRecord,
    ) -> TopicCostRecord:
        async def body(connection: Any) -> TopicCostRecord:
            await self._require_owner(connection, authority)
            _ = await self._lock_topic_targets(connection, authority, [delta.topic_id])
            row = await connection.fetchrow(
                PGTopicStore._UPSERT_COST_SQL,
                delta.topic_id,
                delta.prompt_tokens,
                delta.completion_tokens,
                delta.total_tokens,
                delta.run_count,
                delta.action_count,
                delta.validation_count,
                delta.tool_call_count,
                delta.metadata,
            )
            return _topic_cost_from_row(_required_row(row, "topic cost upsert"))

        return cast(TopicCostRecord, await self._with_transaction(body))

    async def _lock_topic_targets(
        self,
        connection: Any,
        authority: OwnerAuthority,
        topic_ids: list[str],
    ) -> dict[str, str]:
        targets: dict[str, str] = {}
        for topic_id in sorted(set(topic_ids)):
            row = await connection.fetchrow(
                self._SELECT_TOPIC_SESSION_TAPE_SQL, topic_id
            )
            if row is None:
                raise KeyError(f"topic not found: {topic_id}")
            row_dict = dict(row)
            session_id = _required_str(row_dict, "session_id")
            tape_id = _required_str(row_dict, "tape_id")
            if session_id != authority.session_id:
                raise SessionOwnershipConflictError(
                    "topic target belongs to another owner"
                )
            targets[topic_id] = tape_id

        for tape_id in sorted(set(targets.values())):
            await self._require_stable_tape(connection, authority, tape_id)

        for topic_id in sorted(targets):
            row = await connection.fetchrow(
                self._SELECT_TOPIC_SESSION_TAPE_FOR_UPDATE_SQL,
                topic_id,
            )
            if row is None:
                raise KeyError(f"topic not found: {topic_id}")
            row_dict = dict(row)
            session_id = _required_str(row_dict, "session_id")
            tape_id = _required_str(row_dict, "tape_id")
            if session_id != authority.session_id or tape_id != targets[topic_id]:
                raise SessionOwnershipConflictError(
                    "topic target belongs to another owner"
                )
        return targets
