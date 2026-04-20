import pytest
from typing import cast
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

    def test_snapshot_returns_immutable_tuple(self):
        tape = Tape()
        entry = Entry(kind="message", payload={"role": "user", "content": "a"})
        tape.append(entry)

        snapshot = tape.snapshot()

        assert isinstance(snapshot, tuple)
        assert snapshot == (entry,)
        assert not hasattr(snapshot, "append")

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
        # Tests Tape.handoff() directly with a plain Entry bearing meta.is_handoff.
        # This is NOT testing load_jsonl compat — the anchor is passed directly,
        # not loaded from disk. The load_jsonl bare-is_handoff path is covered by
        # test_load_jsonl_new_format_is_handoff.
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
        from agentkit.tape.anchor import Anchor as _Anchor

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
        anchor = cast(_Anchor, tape[1])
        assert isinstance(anchor, _Anchor)
        assert anchor.is_handoff is True
        windowed = tape.windowed_entries()
        assert len(windowed) == 2  # anchor + "after"

    def test_load_jsonl_new_format_is_handoff(self, tmp_path):
        path = tmp_path / "new.jsonl"
        import json as _json
        from agentkit.tape.anchor import Anchor as _Anchor

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
        anchor = cast(_Anchor, tape[1])
        assert isinstance(anchor, _Anchor)
        assert anchor.is_handoff is True
        windowed = tape.windowed_entries()
        assert len(windowed) == 2

    def test_load_jsonl_bare_anchor_without_handoff_metadata_does_not_window(
        self, tmp_path
    ):
        path = tmp_path / "bare_anchor.jsonl"
        import json as _json
        from agentkit.tape.anchor import Anchor as _Anchor

        entries = [
            {
                "id": "c1",
                "kind": "message",
                "payload": {"role": "user", "content": "before"},
                "timestamp": 0,
                "meta": {},
            },
            {
                "id": "c2",
                "kind": "anchor",
                "payload": {"content": "plain anchor"},
                "timestamp": 0,
                "meta": {},
            },
            {
                "id": "c3",
                "kind": "message",
                "payload": {"role": "user", "content": "after"},
                "timestamp": 0,
                "meta": {},
            },
        ]
        path.write_text("\n".join(_json.dumps(e) for e in entries) + "\n")
        tape = Tape.load_jsonl(path)

        assert tape.window_start == 0
        anchor = cast(_Anchor, tape[1])
        assert isinstance(anchor, _Anchor)
        assert anchor.is_handoff is False

    def test_save_jsonl_overwrites_when_persisted_count_unknown(self, tmp_path):
        path = tmp_path / "tape.jsonl"
        path.write_text('{"id":"x","kind":"message","payload":{},"timestamp":0}\n')
        tape = Tape()
        tape.append(Entry(kind="message", payload={"content": "first"}))
        tape.save_jsonl(path)
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 1

    def test_load_jsonl_old_format_meta_topic_finalized(self, tmp_path):
        """Old JSONL with meta.anchor_type=='topic_finalized' loads as Anchor with fold_boundary."""
        import json as _json
        from agentkit.tape.anchor import Anchor as _Anchor

        path = tmp_path / "old_topic.jsonl"
        entries = [
            {
                "id": "m1",
                "kind": "message",
                "payload": {"role": "user", "content": "old msg"},
                "timestamp": 0,
                "meta": {},
            },
            {
                "id": "t1",
                "kind": "anchor",
                "payload": {"content": "Topic involved files: foo.py"},
                "timestamp": 0,
                "meta": {
                    "anchor_type": "topic_finalized",
                    "topic_id": "topic-abc",
                    "files": ["foo.py"],
                },
            },
            {
                "id": "m2",
                "kind": "message",
                "payload": {"role": "user", "content": "new msg"},
                "timestamp": 0,
                "meta": {},
            },
        ]
        path.write_text("\n".join(_json.dumps(e) for e in entries) + "\n")
        tape = Tape.load_jsonl(path)

        assert len(tape) == 3
        # Entry.from_dict promotes meta.anchor_type and bridges topic_finalized→topic_end
        anchor = cast(_Anchor, tape[1])
        assert isinstance(anchor, _Anchor)
        assert anchor.anchor_type == "topic_end"
        assert anchor.fold_boundary is True
        # window_start should stay at 0 (topic_end is not a handoff)
        assert tape.window_start == 0

    def test_load_jsonl_new_anchor_format(self, tmp_path):
        import json as _json
        from agentkit.tape.anchor import Anchor

        path = tmp_path / "new_anchor.jsonl"
        entries = [
            {
                "id": "m1",
                "kind": "message",
                "payload": {"role": "user", "content": "old"},
                "timestamp": 0,
            },
            {
                "id": "a1",
                "kind": "anchor",
                "payload": {"content": "summary"},
                "timestamp": 0,
                "anchor_type": "handoff",
                "source_ids": ["m1"],
            },
            {
                "id": "m2",
                "kind": "message",
                "payload": {"role": "user", "content": "new"},
                "timestamp": 0,
            },
        ]
        path.write_text("\n".join(_json.dumps(e) for e in entries) + "\n")
        tape = Tape.load_jsonl(path)

        assert tape.window_start == 1
        anchor = cast(Anchor, tape[1])
        assert isinstance(anchor, Anchor)
        assert anchor.is_handoff is True
        assert anchor.source_ids == ("m1",)
        windowed = tape.windowed_entries()
        assert len(windowed) == 2

    def test_from_list_restores_window_start_from_handoff_anchor(self):
        from agentkit.tape.anchor import Anchor as _Anchor

        entries_data = [
            {
                "id": "m1",
                "kind": "message",
                "payload": {"content": "old"},
                "timestamp": 0,
                "meta": {},
            },
            {
                "id": "a1",
                "kind": "anchor",
                "payload": {"content": "summary"},
                "timestamp": 0,
                "anchor_type": "handoff",
            },
            {
                "id": "m2",
                "kind": "message",
                "payload": {"content": "new"},
                "timestamp": 0,
                "meta": {},
            },
        ]
        tape = Tape.from_list(entries_data)
        assert tape.window_start == 1
        assert len(tape.windowed_entries()) == 2

    def test_from_list_respects_explicit_window_start_kwarg(self):
        entries_data = [
            {
                "id": "m1",
                "kind": "message",
                "payload": {"content": "old"},
                "timestamp": 0,
                "meta": {},
            },
            {
                "id": "a1",
                "kind": "anchor",
                "payload": {"content": "summary"},
                "timestamp": 0,
                "anchor_type": "handoff",
            },
            {
                "id": "m2",
                "kind": "message",
                "payload": {"content": "new"},
                "timestamp": 0,
                "meta": {},
            },
        ]
        tape = Tape.from_list(entries_data, _window_start=0)
        assert tape.window_start == 0

    def test_from_list_without_anchors_keeps_window_start_zero(self):
        entries_data = [
            {
                "id": "m1",
                "kind": "message",
                "payload": {"content": "a"},
                "timestamp": 0,
                "meta": {},
            },
            {
                "id": "m2",
                "kind": "message",
                "payload": {"content": "b"},
                "timestamp": 0,
                "meta": {},
            },
        ]
        tape = Tape.from_list(entries_data)
        assert tape.window_start == 0

    def test_jsonl_roundtrip_uses_latest_handoff_anchor_for_window_start(
        self, tmp_path
    ):
        import json as _json
        from agentkit.tape.anchor import Anchor

        path = tmp_path / "multi_handoff.jsonl"
        entries = [
            {
                "id": "m1",
                "kind": "message",
                "payload": {"role": "user", "content": "old-1"},
                "timestamp": 0,
                "meta": {},
            },
            {
                "id": "a1",
                "kind": "anchor",
                "payload": {"content": "summary-1"},
                "timestamp": 0,
                "anchor_type": "handoff",
            },
            {
                "id": "m2",
                "kind": "message",
                "payload": {"role": "user", "content": "mid"},
                "timestamp": 0,
                "meta": {},
            },
            {
                "id": "a2",
                "kind": "anchor",
                "payload": {"content": "summary-2"},
                "timestamp": 0,
                "meta": {"is_handoff": True},
            },
            {
                "id": "m3",
                "kind": "message",
                "payload": {"role": "user", "content": "new"},
                "timestamp": 0,
                "meta": {},
            },
        ]
        path.write_text("\n".join(_json.dumps(e) for e in entries) + "\n")

        tape = Tape.load_jsonl(path)

        assert tape.window_start == 3
        anchor = cast(Anchor, tape[3])
        assert isinstance(anchor, Anchor)
        assert anchor.is_handoff is True
        windowed = tape.windowed_entries()
        assert windowed[0] is anchor
        assert len(windowed) == 2
