from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator


class ChildWorkerCoordinator:
    def __init__(self) -> None:
        self._child_sequence = 0
        self._write_lease = asyncio.Lock()

    def allocate_child_id(self, parent_agent_id: str) -> str:
        self._child_sequence += 1
        suffix = f"child-{self._child_sequence}"
        if parent_agent_id:
            return f"{parent_agent_id}.{suffix}"
        return suffix

    @asynccontextmanager
    async def acquire_write_lease(self) -> AsyncIterator[None]:
        async with self._write_lease:
            yield
