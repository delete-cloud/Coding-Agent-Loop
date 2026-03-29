"""ForkTapeStore — transactional tape operations."""

from __future__ import annotations

from agentkit.storage.protocols import TapeStore
from agentkit.tape.tape import Tape


class ForkTapeStore:
    """Transactional layer over a TapeStore.

    Usage:
        fork = store.begin(tape)
        fork.append(entry)
        await store.commit(fork)   # persists
        # OR
        store.rollback(fork)       # discards
    """

    def __init__(self, backing: TapeStore) -> None:
        self._backing = backing
        self._active: dict[str, Tape] = {}
        self._finalized: set[str] = set()

    def begin(self, tape: Tape) -> Tape:
        """Create a transactional fork of the given tape."""
        forked = tape.fork()
        self._active[forked.tape_id] = forked
        return forked

    async def commit(self, fork: Tape) -> None:
        """Persist the fork to the backing store."""
        if fork.tape_id in self._finalized:
            raise ValueError(f"tape '{fork.tape_id}' already finalized")
        self._finalized.add(fork.tape_id)
        self._active.pop(fork.tape_id, None)
        await self._backing.save(fork.tape_id, fork.to_list())

    def rollback(self, fork: Tape) -> None:
        """Discard the fork without persisting."""
        if fork.tape_id in self._finalized:
            raise ValueError(f"tape '{fork.tape_id}' already finalized")
        self._finalized.add(fork.tape_id)
        self._active.pop(fork.tape_id, None)
