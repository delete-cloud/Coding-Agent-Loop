"""Fenced SQLite checkpoint store wrapper."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from agentkit.checkpoint.models import CheckpointMeta, CheckpointSnapshot
from agentkit.storage.sqlite import (
    SQLiteCheckpointStore,
)
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SessionOwnershipConflictError,
)
from coding_agent.stores.local_durable.store import SQLiteLocalDurableStore


class FencedSQLiteCheckpointStore:
    def __init__(
        self,
        *,
        durable_store: SQLiteLocalDurableStore,
        path: Path,
        authority_for_session: Callable[[str], OwnerAuthority],
    ) -> None:
        self._durable_store = durable_store
        self._delegate = SQLiteCheckpointStore(path)
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
        session_id = self._durable_store.session_id_for_checkpoint(checkpoint_id)
        if session_id is None:
            return
        await self._durable_store.delete_checkpoint(
            self._authority_for_session(session_id),
            checkpoint_id,
        )
