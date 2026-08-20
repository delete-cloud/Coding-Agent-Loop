"""Fenced PostgreSQL tape writes."""

from __future__ import annotations

import json
from typing import Any
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
)


class PgTapeMixin:
    async def append_tape_entries(
        self,
        authority: OwnerAuthority,
        tape_id: str,
        entries: list[dict[str, Any]],
    ) -> None:
        if not entries:
            return

        async def body(connection: Any) -> None:
            await self._require_owner(connection, authority)
            await self._require_stable_tape(connection, authority, tape_id)
            payload_values = [json.dumps(entry) for entry in entries]
            await connection.execute(self._INSERT_TAPE_SQL, tape_id, payload_values)

        await self._with_transaction(body)

    async def truncate_tape(
        self,
        authority: OwnerAuthority,
        tape_id: str,
        keep: int,
    ) -> None:
        if keep < 0:
            raise ValueError("keep must be >= 0")

        async def body(connection: Any) -> None:
            await self._require_owner(connection, authority)
            await self._require_stable_tape(connection, authority, tape_id)
            await connection.execute(self._TRUNCATE_TAPE_SQL, tape_id, keep)

        await self._with_transaction(body)
