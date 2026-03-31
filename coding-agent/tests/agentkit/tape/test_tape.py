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

    def test_windowed_entries_default_returns_all(self):
        tape = Tape()
        entries = [Entry(kind="message", payload={"content": str(i)}) for i in range(5)]
        for e in entries:
            tape.append(e)
        windowed = tape.windowed_entries()
        assert len(windowed) == 5
        assert windowed == entries

    def test_handoff_advances_window(self):
        tape = Tape()
        for i in range(10):
            tape.append(Entry(kind="message", payload={"content": str(i)}))
        anchor = Entry(
            kind="anchor",
            payload={"content": "summary"},
            meta={"is_handoff": True},
        )
        tape.handoff(anchor)
        windowed = tape.windowed_entries()
        assert len(windowed) == 1
        assert windowed[0] is anchor

        for i in range(3):
            tape.append(Entry(kind="message", payload={"content": f"new-{i}"}))
        windowed = tape.windowed_entries()
        assert len(windowed) == 4  # anchor + 3

        assert len(tape) == 14  # 10 + 1 anchor + 3

    def test_fork_preserves_window_start(self):
        tape = Tape()
        for i in range(5):
            tape.append(Entry(kind="message", payload={"content": str(i)}))
        anchor = Entry(
            kind="anchor",
            payload={"content": "summary"},
            meta={"is_handoff": True},
        )
        tape.handoff(anchor)
        forked = tape.fork()
        assert forked.window_start == tape.window_start

    def test_handoff_anchor_is_first_windowed_entry(self):
        tape = Tape()
        for i in range(5):
            tape.append(Entry(kind="message", payload={"content": str(i)}))
        anchor = Entry(
            kind="anchor",
            payload={"content": "handoff-anchor"},
            meta={"is_handoff": True},
        )
        tape.handoff(anchor)
        windowed = tape.windowed_entries()
        assert windowed[0] is anchor
        assert windowed[0].payload["content"] == "handoff-anchor"

    def test_jsonl_roundtrip_reconstructs_window_start(self, tmp_path):
        tape = Tape()
        for i in range(5):
            tape.append(Entry(kind="message", payload={"content": str(i)}))
        anchor = Entry(
            kind="anchor",
            payload={"content": "summary"},
            meta={"is_handoff": True},
        )
        tape.handoff(anchor)
        tape.append(Entry(kind="message", payload={"content": "after"}))

        path = tmp_path / "tape.jsonl"
        tape.save_jsonl(path)
        restored = Tape.load_jsonl(path)
        assert len(restored) == 7  # 5 + anchor + 1
        assert restored.window_start == 5  # index of the anchor
        assert restored.windowed_entries()[0].meta.get("is_handoff") is True

    def test_save_jsonl_append_only(self, tmp_path):
        tape = Tape()
        path = tmp_path / "tape.jsonl"
        for i in range(3):
            tape.append(Entry(kind="message", payload={"content": str(i)}))
        tape.save_jsonl(path)
        for i in range(3, 6):
            tape.append(Entry(kind="message", payload={"content": str(i)}))
        tape.save_jsonl(path)
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 6

    def test_save_jsonl_after_load_appends_new_only(self, tmp_path):
        tape = Tape()
        path = tmp_path / "tape.jsonl"
        tape.append(Entry(kind="message", payload={"content": "original"}))
        tape.save_jsonl(path)
        loaded = Tape.load_jsonl(path)
        loaded.append(Entry(kind="message", payload={"content": "new"}))
        loaded.save_jsonl(path)
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_save_jsonl_initial_write_creates_file(self, tmp_path):
        tape = Tape()
        path = tmp_path / "new_tape.jsonl"
        assert not path.exists()
        tape.append(Entry(kind="message", payload={"content": "first"}))
        tape.save_jsonl(path)
        assert path.exists()
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 1

    def test_handoff_with_window_start(self):
        tape = Tape()
        entries = [
            Entry(kind="message", payload={"content": str(i)}) for i in range(14)
        ]
        for e in entries:
            tape.append(e)
        anchor = Entry(
            kind="anchor", payload={"content": "summary"}, meta={"is_handoff": True}
        )
        tape.handoff(anchor, window_start=8)
        windowed = tape.windowed_entries()
        assert len(windowed) == 7  # 6 from entries[8:14] + 1 anchor
        assert windowed == entries[8:14] + [anchor]

    def test_handoff_backward_compat(self):
        tape = Tape()
        for i in range(10):
            tape.append(Entry(kind="message", payload={"content": str(i)}))
        anchor = Entry(
            kind="anchor", payload={"content": "summary"}, meta={"is_handoff": True}
        )
        tape.handoff(anchor)
        windowed = tape.windowed_entries()
        assert len(windowed) == 1
        assert windowed[0] is anchor

    def test_handoff_window_start_zero(self):
        tape = Tape()
        entries = [Entry(kind="message", payload={"content": str(i)}) for i in range(5)]
        for e in entries:
            tape.append(e)
        anchor = Entry(
            kind="anchor", payload={"content": "summary"}, meta={"is_handoff": True}
        )
        tape.handoff(anchor, window_start=0)
        windowed = tape.windowed_entries()
        assert len(windowed) == 6  # 5 original + 1 anchor
        assert windowed == entries + [anchor]

    def test_load_jsonl_old_format_backward_compat(self, tmp_path):
        path = tmp_path / "old.jsonl"
        import json as _json
        from agentkit.tape.models import Entry as _Entry

        entries = [
            {
                "id": "a1",
                "kind": "message",
                "payload": {"role": "user", "content": "hi"},
                "timestamp": 0,
                "meta": {},
            },
            {
                "id": "a2",
                "kind": "anchor",
                "payload": {"content": "summary"},
                "timestamp": 0,
                "meta": {"anchor_type": "handoff", "source_entry_count": 1},
            },
            {
                "id": "a3",
                "kind": "message",
                "payload": {"role": "user", "content": "after"},
                "timestamp": 0,
                "meta": {},
            },
        ]
        path.write_text("\n".join(_json.dumps(e) for e in entries) + "\n")
        tape = Tape.load_jsonl(path)
        assert tape.window_start == 1
        assert tape[1].meta.get("is_handoff") is True
        windowed = tape.windowed_entries()
        assert len(windowed) == 2  # anchor + "after"

    def test_load_jsonl_new_format_is_handoff(self, tmp_path):
        path = tmp_path / "new.jsonl"
        import json as _json

        entries = [
            {
                "id": "b1",
                "kind": "message",
                "payload": {"role": "user", "content": "old"},
                "timestamp": 0,
                "meta": {},
            },
            {
                "id": "b2",
                "kind": "anchor",
                "payload": {"content": "summary"},
                "timestamp": 0,
                "meta": {"is_handoff": True},
            },
            {
                "id": "b3",
                "kind": "message",
                "payload": {"role": "user", "content": "new"},
                "timestamp": 0,
                "meta": {},
            },
        ]
        path.write_text("\n".join(_json.dumps(e) for e in entries) + "\n")
        tape = Tape.load_jsonl(path)
        assert tape.window_start == 1
        windowed = tape.windowed_entries()
        assert len(windowed) == 2

    def test_save_jsonl_overwrites_when_persisted_count_unknown(self, tmp_path):
        path = tmp_path / "tape.jsonl"
        path.write_text('{"id":"x","kind":"message","payload":{},"timestamp":0}\n')
        tape = Tape()
        tape.append(Entry(kind="message", payload={"content": "first"}))
        tape.save_jsonl(path)
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 1
