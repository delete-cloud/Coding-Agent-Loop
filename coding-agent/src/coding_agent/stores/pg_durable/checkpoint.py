"""Fenced PostgreSQL checkpoint save/restore."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any
from agentkit.checkpoint.models import CheckpointSnapshot
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SessionOwnershipConflictError,
)
from coding_agent.stores.pg_durable.helpers import (
    _checkpoint_meta_payload,
    _require_payload_session,
    _required_owned_row,
)
from coding_agent.runtime_activation import assert_checkpoint_allowed


class PgCheckpointMixin:
    async def save_checkpoint(
        self,
        authority: OwnerAuthority,
        snapshot: CheckpointSnapshot,
    ) -> None:
        meta = snapshot.meta
        if meta.session_id != authority.session_id:
            raise SessionOwnershipConflictError(
                "checkpoint target belongs to another owner"
            )
        payload = await self.load_session_payload(authority.session_id)
        if payload is not None:
            assert_checkpoint_allowed(payload)

        async def body(connection: Any) -> None:
            await self._require_owner(connection, authority)
            await self._require_stable_tape(connection, authority, meta.tape_id)
            row = await connection.fetchrow(
                self._UPSERT_OWNED_CHECKPOINT_SQL,
                meta.checkpoint_id,
                meta.tape_id,
                _checkpoint_meta_payload(meta),
                list(snapshot.tape_entries),
                snapshot.plugin_states,
                snapshot.extra,
                authority.session_id,
            )
            _required_owned_row(row, "checkpoint target belongs to another owner")

        await self._with_transaction(body)

    async def delete_checkpoint(
        self,
        authority: OwnerAuthority,
        checkpoint_id: str,
    ) -> None:
        async def body(connection: Any) -> None:
            await self._require_owner(connection, authority)
            await self._require_checkpoint_owner(connection, authority, checkpoint_id)
            await connection.execute(self._DELETE_CHECKPOINT_SQL, checkpoint_id)

        await self._with_transaction(body)

    async def restore_checkpoint_state(
        self,
        authority: OwnerAuthority,
        snapshot: CheckpointSnapshot,
        session_payload: dict[str, Any],
    ) -> None:
        assert_checkpoint_allowed(session_payload)
        _require_payload_session(authority, session_payload)
        meta = snapshot.meta
        if meta.session_id != authority.session_id:
            raise SessionOwnershipConflictError(
                "checkpoint target belongs to another owner"
            )
        if session_payload.get("tape_id") != meta.tape_id:
            raise SessionOwnershipConflictError(
                "checkpoint restore session payload has mismatched tape id"
            )

        async def body(connection: Any) -> None:
            await self._require_owner(connection, authority)
            await self._require_stable_tape(connection, authority, meta.tape_id)
            await self._require_checkpoint_owner(
                connection,
                authority,
                meta.checkpoint_id,
            )
            await connection.execute(self._TRUNCATE_TAPE_SQL, meta.tape_id, 0)
            if snapshot.tape_entries:
                payload_values = [json.dumps(entry) for entry in snapshot.tape_entries]
                await connection.execute(
                    self._INSERT_TAPE_SQL, meta.tape_id, payload_values
                )
            await connection.execute(
                self._UPSERT_SESSION_SQL,
                authority.session_id,
                session_payload,
            )
            await connection.execute(
                self._SUPERSEDE_RUNS_AFTER_CHECKPOINT_SQL,
                authority.session_id,
                meta.created_at,
                meta.checkpoint_id,
            )
            await self._reconcile_topics_after_checkpoint_restore(
                connection,
                tape_id=meta.tape_id,
                entry_count=meta.entry_count,
                checkpoint_created_at=meta.created_at,
            )
            await connection.execute(
                self._DELETE_NEWER_CHECKPOINTS_SQL,
                meta.tape_id,
                authority.session_id,
                meta.entry_count,
            )
            await connection.execute(
                self._DELETE_TURN_MAILBOX_SLOTS_SQL,
                authority.session_id,
            )
            await self._open_projection_epoch(connection, authority.session_id)

        await self._with_transaction(body)

    async def _reconcile_topics_after_checkpoint_restore(
        self,
        connection: Any,
        *,
        tape_id: str,
        entry_count: int,
        checkpoint_created_at: datetime,
    ) -> None:
        if not tape_id:
            raise ValueError("tape_id must be non-empty")
        if entry_count < 0:
            raise ValueError("entry_count must be >= 0")
        await connection.execute(
            self._DELETE_TOPIC_RECALL_LINKS_FOR_TAPE_SQL,
            tape_id,
        )
        await connection.execute(
            self._DELETE_TOPIC_COSTS_FOR_TAPE_SQL,
            tape_id,
        )
        await connection.execute(
            self._DELETE_TOPIC_ANCHORS_AFTER_CHECKPOINT_SQL,
            tape_id,
            entry_count,
        )
        await connection.execute(
            self._DELETE_TOPICS_AFTER_CHECKPOINT_SQL,
            tape_id,
            entry_count,
            checkpoint_created_at,
        )
        await connection.execute(
            self._REOPEN_TOPICS_CLOSED_AFTER_CHECKPOINT_SQL,
            tape_id,
            entry_count,
            checkpoint_created_at,
        )
