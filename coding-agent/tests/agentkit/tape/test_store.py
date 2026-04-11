import pytest
from agentkit.tape.store import ForkTapeStore
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry


class InMemoryTapeStore:
    def __init__(self):
        self._tapes = {}

    async def save(self, tape_id, entries):
        if tape_id not in self._tapes:
            self._tapes[tape_id] = []
        self._tapes[tape_id].extend(entries)

    async def load(self, tape_id):
        return self._tapes.get(tape_id, [])

    async def list_ids(self):
        return list(self._tapes.keys())


class TestForkTapeStore:
    @pytest.fixture
    def backing_store(self):
        return InMemoryTapeStore()

    @pytest.fixture
    def fork_store(self, backing_store):
        return ForkTapeStore(backing_store)

    @pytest.mark.asyncio
    async def test_begin_creates_fork(self, fork_store):
        tape = Tape()
        tape.append(Entry(kind="message", payload={"content": "original"}))
        forked = fork_store.begin(tape)
        assert forked.parent_id == tape.tape_id
        assert len(forked) == 1

    @pytest.mark.asyncio
    async def test_fork_is_independent(self, fork_store):
        tape = Tape()
        tape.append(Entry(kind="message", payload={"content": "original"}))
        forked = fork_store.begin(tape)
        forked.append(Entry(kind="message", payload={"content": "fork-addition"}))
        assert len(tape) == 1
        assert len(forked) == 2

    @pytest.mark.asyncio
    async def test_commit_persists(self, fork_store, backing_store):
        tape = Tape()
        tape.append(Entry(kind="message", payload={"content": "original"}))
        forked = fork_store.begin(tape)
        forked.append(Entry(kind="tool_call", payload={"name": "bash"}))
        await fork_store.commit(forked)
        loaded = await backing_store.load(forked.tape_id)
        assert len(loaded) == 1  # only delta (new entry) is persisted

    @pytest.mark.asyncio
    async def test_rollback_discards(self, fork_store, backing_store):
        tape = Tape()
        forked = fork_store.begin(tape)
        forked.append(Entry(kind="message", payload={"content": "will be discarded"}))
        fork_store.rollback(forked)
        loaded = await backing_store.load(forked.tape_id)
        assert loaded == []

    @pytest.mark.asyncio
    async def test_commit_after_rollback_raises(self, fork_store):
        tape = Tape()
        forked = fork_store.begin(tape)
        fork_store.rollback(forked)
        with pytest.raises(ValueError, match="already finalized"):
            await fork_store.commit(forked)

    @pytest.mark.asyncio
    async def test_commit_only_saves_delta(self, fork_store, backing_store):
        tape = Tape()
        for i in range(5):
            tape.append(Entry(kind="message", payload={"content": str(i)}))
        forked = fork_store.begin(tape)
        for i in range(5, 8):
            forked.append(Entry(kind="message", payload={"content": str(i)}))
        await fork_store.commit(forked)
        loaded = await backing_store.load(forked.tape_id)
        assert len(loaded) == 3  # only the 3 new entries, not all 8
