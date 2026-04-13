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

    async def truncate(self, tape_id, keep):
        if tape_id not in self._tapes:
            return
        self._tapes[tape_id] = self._tapes[tape_id][:keep]


class FailingOnceTapeStore(InMemoryTapeStore):
    def __init__(self):
        super().__init__()
        self.calls = 0

    async def save(self, tape_id, entries):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient save failure")
        await super().save(tape_id, entries)


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
        stable_id = await fork_store.commit(forked)
        loaded = await backing_store.load(tape.tape_id)
        assert len(loaded) == 1  # only delta (new entry) is persisted
        assert stable_id == tape.tape_id

    @pytest.mark.asyncio
    async def test_rollback_discards(self, fork_store, backing_store):
        tape = Tape()
        forked = fork_store.begin(tape)
        forked.append(Entry(kind="message", payload={"content": "will be discarded"}))
        fork_store.rollback(forked)
        loaded = await backing_store.load(forked.tape_id)
        assert loaded == []

    @pytest.mark.asyncio
    async def test_rollback_cleans_internal_tracking_state(self, fork_store):
        tape = Tape()
        forked = fork_store.begin(tape)

        fork_store.rollback(forked)

        assert forked.tape_id not in fork_store._base_lengths
        assert forked.tape_id not in fork_store._base_tape_ids

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
        loaded = await backing_store.load(tape.tape_id)
        assert len(loaded) == 3  # only the 3 new entries, not all 8

    @pytest.mark.asyncio
    async def test_second_commit_appends_to_same_base_tape_id(
        self, fork_store, backing_store
    ):
        first_tape = Tape()
        first_fork = fork_store.begin(first_tape)
        first_fork.append(Entry(kind="message", payload={"content": "turn-1"}))

        stable_id = await fork_store.commit(first_fork)
        assert stable_id == first_tape.tape_id

        persisted_entries = await backing_store.load(stable_id)
        second_tape = Tape.from_list(persisted_entries, tape_id=stable_id)
        second_fork = fork_store.begin(second_tape)
        second_fork.append(Entry(kind="message", payload={"content": "turn-2"}))

        second_stable_id = await fork_store.commit(second_fork)
        loaded = await backing_store.load(stable_id)

        assert second_stable_id == stable_id
        assert [entry["payload"]["content"] for entry in loaded] == ["turn-1", "turn-2"]

    @pytest.mark.asyncio
    async def test_commit_save_failure_does_not_duplicate_delta_on_retry(self):
        backing_store = FailingOnceTapeStore()
        fork_store = ForkTapeStore(backing_store)
        tape = Tape()
        forked = fork_store.begin(tape)
        forked.append(Entry(kind="message", payload={"content": "turn-1"}))

        with pytest.raises(RuntimeError, match="transient save failure"):
            await fork_store.commit(forked)

        stable_id = await fork_store.commit(forked)
        loaded = await backing_store.load(tape.tape_id)

        assert stable_id == tape.tape_id
        assert [entry["payload"]["content"] for entry in loaded] == ["turn-1"]
