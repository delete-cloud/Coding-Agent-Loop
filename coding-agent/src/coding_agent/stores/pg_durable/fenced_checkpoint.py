"""Fenced PostgreSQL checkpoint store wrapper."""

from __future__ import annotations

from collections.abc import Callable
from agentkit.checkpoint.models import CheckpointMeta, CheckpointSnapshot
from agentkit.storage.pg import (
    PGCheckpointStore,
    PGPool,
)
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SessionOwnershipConflictError,
)
from coding_agent.stores.pg_durable.store import PGDurableStore


class FencedPGCheckpointStore:
    def __init__(
        self,
        *,
        durable_store: PGDurableStore,
        pool: PGPool,
        authority_for_session: Callable[[str], OwnerAuthority],
    ) -> None:
        self._durable_store = durable_store
        self._delegate = PGCheckpointStore(pool=pool)
        self._authority_for_session = authority_for_session

    async def save(self, snapshot: CheckpointSnapshot) -> None:
        session_id = snapshot.meta.session_id
        if session_id is None:
            raise SessionOwnershipConflictError(
                "checkpoint target is not bound to a session"
            )
        await self._durable_store.save_checkpoint(
            self._authority_for_session(session_id),
            snapshot,
        )

    async def load(self, checkpoint_id: str) -> CheckpointSnapshot | None:
        return await self._delegate.load(checkpoint_id)

    async def list_by_tape(self, tape_id: str) -> list[CheckpointMeta]:
        return await self._delegate.list_by_tape(tape_id)

    async def delete(self, checkpoint_id: str) -> None:
        snapshot = await self._delegate.load(checkpoint_id)
        if snapshot is None:
            return
        session_id = snapshot.meta.session_id
        if session_id is None:
            raise SessionOwnershipConflictError(
                "checkpoint target is not bound to a session"
            )
        await self._durable_store.delete_checkpoint(
            self._authority_for_session(session_id),
            checkpoint_id,
        )
