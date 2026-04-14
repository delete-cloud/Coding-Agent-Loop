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
        self._base_lengths: dict[str, int] = {}
        self._base_tape_ids: dict[str, str] = {}

    def begin(self, tape: Tape) -> Tape:
        """Create a transactional fork of the given tape."""
        forked = tape.fork()
        self._active[forked.tape_id] = forked
        self._base_lengths[forked.tape_id] = len(tape)
        self._base_tape_ids[forked.tape_id] = tape.tape_id
        return forked

    async def commit(self, fork: Tape) -> str:
        if fork.tape_id in self._finalized:
            raise ValueError(f"tape '{fork.tape_id}' already finalized")
        base_length = self._base_lengths[fork.tape_id]
        stable_tape_id = self._base_tape_ids[fork.tape_id]
        delta = fork.to_list()[base_length:]
        self._finalized.add(fork.tape_id)
        try:
            await self._backing.save(stable_tape_id, delta)
        except Exception:
            self._finalized.discard(fork.tape_id)
            raise
        self._active.pop(fork.tape_id, None)
        self._base_lengths.pop(fork.tape_id, None)
        self._base_tape_ids.pop(fork.tape_id, None)
        return stable_tape_id

    def rollback(self, fork: Tape) -> None:
        """Discard the fork without persisting."""
        if fork.tape_id in self._finalized:
            raise ValueError(f"tape '{fork.tape_id}' already finalized")
        self._finalized.add(fork.tape_id)
        self._active.pop(fork.tape_id, None)
        self._base_lengths.pop(fork.tape_id, None)
        self._base_tape_ids.pop(fork.tape_id, None)
