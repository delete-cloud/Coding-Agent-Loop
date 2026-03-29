import pytest
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry


class TestTape:
    def test_empty_tape(self):
        tape = Tape()
        assert len(tape) == 0
        assert list(tape) == []

    def test_append_entry(self):
        tape = Tape()
        entry = Entry(kind="message", payload={"role": "user", "content": "hi"})
        tape.append(entry)
        assert len(tape) == 1
        assert tape[0] is entry

    def test_iterate_entries(self):
        tape = Tape()
        e1 = Entry(kind="message", payload={"role": "user", "content": "a"})
        e2 = Entry(kind="message", payload={"role": "assistant", "content": "b"})
        tape.append(e1)
        tape.append(e2)
        assert list(tape) == [e1, e2]

    def test_slice(self):
        tape = Tape()
        entries = [Entry(kind="message", payload={"content": str(i)}) for i in range(5)]
        for e in entries:
            tape.append(e)
        assert tape[1:3] == entries[1:3]

    def test_filter_by_kind(self):
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hi"}))
        tape.append(Entry(kind="tool_call", payload={"name": "bash"}))
        tape.append(
            Entry(kind="message", payload={"role": "assistant", "content": "ok"})
        )
        messages = tape.filter(kind="message")
        assert len(messages) == 2

    def test_fork_creates_independent_copy(self):
        tape = Tape()
        tape.append(Entry(kind="message", payload={"content": "original"}))
        forked = tape.fork()
        forked.append(Entry(kind="message", payload={"content": "fork-only"}))
        assert len(tape) == 1
        assert len(forked) == 2

    def test_fork_preserves_parent_id(self):
        tape = Tape(tape_id="parent-1")
        forked = tape.fork()
        assert forked.parent_id == "parent-1"
        assert forked.tape_id != "parent-1"

    def test_serialize_roundtrip(self):
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hi"}))
        tape.append(Entry(kind="tool_call", payload={"name": "bash"}))
        data = tape.to_list()
        restored = Tape.from_list(data)
        assert len(restored) == 2
        assert restored[0].kind == "message"
        assert restored[1].kind == "tool_call"

    def test_jsonl_roundtrip(self, tmp_path):
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hi"}))
        path = tmp_path / "tape.jsonl"
        tape.save_jsonl(path)
        restored = Tape.load_jsonl(path)
        assert len(restored) == 1
        assert restored[0].payload["content"] == "hi"
