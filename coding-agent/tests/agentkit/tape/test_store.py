import pytest
from agentkit.tape.store import ForkTapeStore
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry


class InMemoryTapeStore:
    def __init__(self):
        self._tapes = {}

    async def save(self, tape_id, entries):
        self._tapes[tape_id] = entries

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
        assert len(loaded) == 2

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
