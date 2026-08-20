"""Fenced topic lifecycle and cost/recall writes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from coding_agent.topics.store import (
    TopicAnchorRecord,
    TopicCostRecord,
    TopicRecallLinkRecord,
    TopicRecord,
    TopicStatus,
    _datetime_to_sqlite_text as _topic_datetime_to_sqlite_text,
    _json_to_sqlite_text as _topic_json_to_sqlite_text,
    _optional_datetime_to_sqlite_text as _topic_optional_datetime_to_sqlite_text,
    _require_datetime as _topic_require_datetime,
    _require_json_object as _topic_require_json_object,
    _require_non_empty as _topic_require_non_empty,
    _require_non_negative_int as _topic_require_non_negative_int,
    _require_optional_display_text as _topic_require_optional_display_text,
    _required_sqlite_row,
    _topic_anchor_from_sqlite_row,
    _topic_cost_from_sqlite_row,
    _topic_from_sqlite_row,
    _topic_recall_link_from_sqlite_row,
)
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SessionOwnershipConflictError,
)


class LocalTopicsMixin:
    def session_id_for_topic(self, topic_id: str) -> str | None:
        _topic_require_non_empty("topic_id", topic_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT session_id FROM topics WHERE topic_id = ?",
                (topic_id,),
            ).fetchone()
        if row is None:
            return None
        session_id = row["session_id"]
        if not isinstance(session_id, str):
            raise TypeError("topic session_id must be text")
        return session_id

    async def create_topic(
        self,
        authority: OwnerAuthority,
        record: TopicRecord,
    ) -> TopicRecord:
        if record.session_id != authority.session_id:
            raise SessionOwnershipConflictError("topic target belongs to another owner")
        now = _topic_datetime_to_sqlite_text(datetime.now(UTC))
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            self._assert_tape_belongs_to_session(
                connection,
                tape_id=record.tape_id,
                session_id=authority.session_id,
            )
            connection.execute(
                """
                INSERT INTO topics (
                    topic_id,
                    tape_id,
                    session_id,
                    kind,
                    status,
                    title,
                    summary,
                    owner,
                    topic_initial_seq,
                    topic_finalized_seq,
                    created_at,
                    finalized_at,
                    metadata,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(topic_id) DO NOTHING
                """,
                (
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
                    _topic_datetime_to_sqlite_text(record.created_at),
                    _topic_optional_datetime_to_sqlite_text(record.finalized_at),
                    _topic_json_to_sqlite_text(record.metadata),
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM topics WHERE topic_id = ?",
                (record.topic_id,),
            ).fetchone()
            topic = _topic_from_sqlite_row(_required_sqlite_row(row, "topic insert"))
            if (
                topic.session_id != authority.session_id
                or topic.tape_id != record.tape_id
            ):
                raise SessionOwnershipConflictError(
                    "topic target belongs to another owner"
                )
            return topic

    async def finalize_topic(
        self,
        authority: OwnerAuthority,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int,
        finalized_at: datetime,
        metadata: dict[str, Any],
    ) -> TopicRecord:
        return await self._close_topic(
            authority,
            "finalized",
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
        metadata: dict[str, Any],
    ) -> TopicRecord:
        return await self._close_topic(
            authority,
            "aborted",
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
        status: TopicStatus,
        operation: str,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int | None,
        finalized_at: datetime,
        metadata: dict[str, Any],
    ) -> TopicRecord:
        _topic_require_non_empty("topic_id", topic_id)
        _topic_require_optional_display_text("summary", summary)
        if topic_finalized_seq is not None:
            _topic_require_non_negative_int("topic_finalized_seq", topic_finalized_seq)
        _topic_require_datetime("finalized_at", finalized_at)
        _topic_require_json_object("metadata", metadata)
        if status == "finalized" and topic_finalized_seq is None:
            raise ValueError("topic_finalized_seq must be provided for finalize")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            self._assert_topic_belongs_to_authority(connection, authority, topic_id)
            if status == "finalized":
                row = connection.execute(
                    """
                    UPDATE topics
                    SET status = ?,
                        summary = ?,
                        topic_finalized_seq = ?,
                        finalized_at = ?,
                        metadata = ?,
                        updated_at = ?
                    WHERE topic_id = ? AND status = 'open'
                      AND ? >= topic_initial_seq
                    RETURNING *
                    """,
                    (
                        status,
                        summary,
                        topic_finalized_seq,
                        _topic_datetime_to_sqlite_text(finalized_at),
                        _topic_json_to_sqlite_text(metadata),
                        _topic_datetime_to_sqlite_text(datetime.now(UTC)),
                        topic_id,
                        topic_finalized_seq,
                    ),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    UPDATE topics
                    SET status = ?,
                        summary = ?,
                        topic_finalized_seq = ?,
                        finalized_at = ?,
                        metadata = ?,
                        updated_at = ?
                    WHERE topic_id = ? AND status = 'open'
                      AND (? IS NULL OR ? >= topic_initial_seq)
                    RETURNING *
                    """,
                    (
                        status,
                        summary,
                        topic_finalized_seq,
                        _topic_datetime_to_sqlite_text(finalized_at),
                        _topic_json_to_sqlite_text(metadata),
                        _topic_datetime_to_sqlite_text(datetime.now(UTC)),
                        topic_id,
                        topic_finalized_seq,
                        topic_finalized_seq,
                    ),
                ).fetchone()
        if row is None:
            raise KeyError(f"open topic not found for {operation}: {topic_id}")
        return _topic_from_sqlite_row(row)

    async def delete_topic(
        self,
        authority: OwnerAuthority,
        topic_id: str,
    ) -> None:
        _topic_require_non_empty("topic_id", topic_id)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            self._assert_topic_belongs_to_authority(connection, authority, topic_id)
            connection.execute(
                """
                DELETE FROM topic_recall_links
                WHERE source_topic_id = ? OR recalled_topic_id = ?
                """,
                (topic_id, topic_id),
            )
            connection.execute(
                "DELETE FROM topic_costs WHERE topic_id = ?",
                (topic_id,),
            )
            connection.execute(
                "DELETE FROM topic_anchors WHERE topic_id = ?",
                (topic_id,),
            )
            connection.execute(
                "DELETE FROM topics WHERE topic_id = ?",
                (topic_id,),
            )

    async def record_topic_anchor(
        self,
        authority: OwnerAuthority,
        record: TopicAnchorRecord,
    ) -> TopicAnchorRecord:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            topic_tape_id = self._assert_topic_belongs_to_authority(
                connection,
                authority,
                record.topic_id,
            )
            if record.tape_id != topic_tape_id:
                raise SessionOwnershipConflictError(
                    "topic anchor target belongs to another tape"
                )
            row = connection.execute(
                """
                INSERT INTO topic_anchors (
                    topic_id,
                    tape_id,
                    seq,
                    anchor_type,
                    entry_id,
                    metadata,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(topic_id, seq, anchor_type)
                DO UPDATE SET
                    tape_id = excluded.tape_id,
                    entry_id = excluded.entry_id,
                    metadata = excluded.metadata
                RETURNING *
                """,
                (
                    record.topic_id,
                    record.tape_id,
                    record.seq,
                    record.anchor_type,
                    record.entry_id,
                    _topic_json_to_sqlite_text(record.metadata),
                    _topic_datetime_to_sqlite_text(
                        record.created_at or datetime.now(UTC)
                    ),
                ),
            ).fetchone()
        return _topic_anchor_from_sqlite_row(
            _required_sqlite_row(row, "topic anchor upsert")
        )

    async def record_recall_link(
        self,
        authority: OwnerAuthority,
        record: TopicRecallLinkRecord,
    ) -> TopicRecallLinkRecord:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            self._assert_topic_belongs_to_authority(
                connection,
                authority,
                record.source_topic_id,
            )
            self._assert_topic_belongs_to_authority(
                connection,
                authority,
                record.recalled_topic_id,
            )
            row = connection.execute(
                """
                INSERT INTO topic_recall_links (
                    source_topic_id,
                    recalled_topic_id,
                    relation,
                    anchor_seq,
                    source_entry_start_seq,
                    source_entry_end_seq,
                    metadata,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_topic_id, recalled_topic_id, relation)
                DO UPDATE SET
                    anchor_seq = excluded.anchor_seq,
                    source_entry_start_seq = excluded.source_entry_start_seq,
                    source_entry_end_seq = excluded.source_entry_end_seq,
                    metadata = excluded.metadata
                RETURNING *
                """,
                (
                    record.source_topic_id,
                    record.recalled_topic_id,
                    record.relation,
                    record.anchor_seq,
                    record.source_entry_start_seq,
                    record.source_entry_end_seq,
                    _topic_json_to_sqlite_text(record.metadata),
                    _topic_datetime_to_sqlite_text(
                        record.created_at or datetime.now(UTC)
                    ),
                ),
            ).fetchone()
        return _topic_recall_link_from_sqlite_row(
            _required_sqlite_row(row, "topic recall link upsert")
        )

    async def update_topic_cost(
        self,
        authority: OwnerAuthority,
        delta: TopicCostRecord,
    ) -> TopicCostRecord:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            self._assert_topic_belongs_to_authority(
                connection,
                authority,
                delta.topic_id,
            )
            row = connection.execute(
                """
                INSERT INTO topic_costs (
                    topic_id,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    run_count,
                    action_count,
                    validation_count,
                    tool_call_count,
                    metadata,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(topic_id)
                DO UPDATE SET
                    prompt_tokens = topic_costs.prompt_tokens
                        + excluded.prompt_tokens,
                    completion_tokens = topic_costs.completion_tokens
                        + excluded.completion_tokens,
                    total_tokens = topic_costs.total_tokens
                        + excluded.total_tokens,
                    run_count = topic_costs.run_count + excluded.run_count,
                    action_count = topic_costs.action_count + excluded.action_count,
                    validation_count = topic_costs.validation_count
                        + excluded.validation_count,
                    tool_call_count = topic_costs.tool_call_count
                        + excluded.tool_call_count,
                    metadata = excluded.metadata,
                    updated_at = excluded.updated_at
                RETURNING *
                """,
                (
                    delta.topic_id,
                    delta.prompt_tokens,
                    delta.completion_tokens,
                    delta.total_tokens,
                    delta.run_count,
                    delta.action_count,
                    delta.validation_count,
                    delta.tool_call_count,
                    _topic_json_to_sqlite_text(delta.metadata),
                    _topic_datetime_to_sqlite_text(
                        delta.updated_at or datetime.now(UTC)
                    ),
                ),
            ).fetchone()
        return _topic_cost_from_sqlite_row(
            _required_sqlite_row(row, "topic cost upsert")
        )
