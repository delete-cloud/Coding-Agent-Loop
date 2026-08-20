"""Fenced SQLite tape store wrapper."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from agentkit.storage.protocols import TapeInfo, TapeSearchResult
from agentkit.storage.sqlite import (
    SQLiteTapeStore,
)
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SessionOwnershipConflictError,
)
from coding_agent.stores.local_durable.store import SQLiteLocalDurableStore


class FencedSQLiteTapeStore:
    def __init__(
        self,
        *,
        durable_store: SQLiteLocalDurableStore,
        path: Path,
        authority_for_session: Callable[[str], OwnerAuthority],
    ) -> None:
        self._durable_store = durable_store
        self._delegate = SQLiteTapeStore(path)
        self._authority_for_session = authority_for_session

    async def save(self, tape_id: str, entries: list[dict[str, Any]]) -> None:
        session_id = self._require_session_id_for_tape(tape_id)
        await self._durable_store.append_tape_entries(
            self._authority_for_session(session_id),
            tape_id,
            entries,
        )

    async def load(self, tape_id: str) -> list[dict[str, Any]]:
        return await self._delegate.load(tape_id)

    async def list_ids(self) -> list[str]:
        return await self._delegate.list_ids()

    async def truncate(self, tape_id: str, keep: int) -> None:
        session_id = self._require_session_id_for_tape(tape_id)
        await self._durable_store.truncate_tape(
            self._authority_for_session(session_id),
            tape_id,
            keep,
        )

    async def info(self, tape_id: str) -> TapeInfo | None:
        return await self._delegate.info(tape_id)

    async def search(
        self,
        *,
        tape_id: str | None = None,
        kind: str | None = None,
        run_id: str | None = None,
        tool_call_id: str | None = None,
        anchor_type: str | None = None,
        limit: int = 100,
    ) -> list[TapeSearchResult]:
        return await self._delegate.search(
            tape_id=tape_id,
            kind=kind,
            run_id=run_id,
            tool_call_id=tool_call_id,
            anchor_type=anchor_type,
            limit=limit,
        )

    def _require_session_id_for_tape(self, tape_id: str) -> str:
        session_id = self._durable_store.session_id_for_tape(tape_id)
        if session_id is None:
            raise SessionOwnershipConflictError("tape target is not bound to a session")
        return session_id
