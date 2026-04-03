import pytest
from agentkit.tape.models import Entry, EntryKind
from datetime import datetime


class TestEntry:
    def test_create_message_entry(self):
        entry = Entry(kind="message", payload={"role": "user", "content": "hello"})
        assert entry.kind == "message"
        assert entry.payload["content"] == "hello"
        assert entry.id  # auto-generated UUID
        assert isinstance(entry.timestamp, float)

    def test_entry_is_frozen(self):
        entry = Entry(kind="message", payload={"role": "user", "content": "hi"})
        with pytest.raises(AttributeError):
            entry.kind = "tool_call"

    def test_entry_kinds(self):
        for kind in ("message", "tool_call", "tool_result", "anchor", "event"):
            entry = Entry(kind=kind, payload={})
            assert entry.kind == kind

    def test_entry_to_dict(self):
        entry = Entry(kind="message", payload={"role": "user", "content": "hi"})
        d = entry.to_dict()
        assert d["kind"] == "message"
        assert d["payload"]["content"] == "hi"
        assert "id" in d
        assert "timestamp" in d

    def test_entry_from_dict(self):
        d = {
            "id": "abc-123",
            "kind": "message",
            "payload": {"role": "user", "content": "hi"},
            "timestamp": 1000.0,
        }
        entry = Entry.from_dict(d)
        assert entry.id == "abc-123"
        assert entry.kind == "message"
        assert entry.payload["content"] == "hi"
        assert entry.timestamp == 1000.0

    def test_entry_roundtrip(self):
        original = Entry(
            kind="tool_call",
            payload={"name": "file_read", "arguments": {"path": "/a.py"}},
        )
        restored = Entry.from_dict(original.to_dict())
        assert restored.id == original.id
        assert restored.kind == original.kind
        assert restored.payload == original.payload

    def test_entry_default_meta_is_empty_dict(self):
        entry = Entry(kind="message", payload={"role": "user", "content": "hi"})
        assert entry.meta == {}

    def test_entry_custom_meta(self):
        entry = Entry(
            kind="message",
            payload={"role": "user", "content": "hi"},
            meta={"tokens": 150, "anchor_type": "summary"},
        )
        assert entry.meta["tokens"] == 150
        assert entry.meta["anchor_type"] == "summary"

    def test_entry_meta_roundtrip(self):
        original = Entry(
            kind="message",
            payload={"role": "user", "content": "hi"},
            meta={"tokens": 150, "anchor_type": "summary"},
        )
        restored = Entry.from_dict(original.to_dict())
        assert restored.meta == original.meta

    def test_entry_from_dict_missing_meta_defaults_empty(self):
        d = {
            "id": "abc-123",
            "kind": "message",
            "payload": {"role": "user", "content": "hi"},
            "timestamp": 1000.0,
        }
        entry = Entry.from_dict(d)
        assert entry.meta == {}

    def test_from_dict_returns_anchor_for_new_format(self):
        from agentkit.tape.anchor import Anchor

        d = {
            "id": "a1",
            "kind": "anchor",
            "payload": {"content": "summary"},
            "timestamp": 1000.0,
            "anchor_type": "handoff",
            "source_ids": ["id1", "id2"],
        }
        entry = Entry.from_dict(d)
        assert isinstance(entry, Anchor)
        assert entry.anchor_type == "handoff"
        assert entry.source_ids == ("id1", "id2")

    def test_from_dict_returns_plain_entry_for_old_anchor_format(self):
        d = {
            "id": "a2",
            "kind": "anchor",
            "payload": {"content": "old summary"},
            "timestamp": 1000.0,
            "meta": {"is_handoff": True},
        }
        entry = Entry.from_dict(d)
        assert type(entry) is Entry
        assert entry.kind == "anchor"
        assert entry.meta["is_handoff"] is True

    def test_from_dict_returns_plain_entry_for_non_anchor(self):
        d = {
            "id": "m1",
            "kind": "message",
            "payload": {"role": "user", "content": "hi"},
            "timestamp": 1000.0,
        }
        entry = Entry.from_dict(d)
        assert type(entry) is Entry

    def test_from_dict_dispatches_old_format_meta_anchor_type(self):
        from agentkit.tape.anchor import Anchor

        d = {
            "id": "old-1",
            "kind": "anchor",
            "payload": {"content": "old summary"},
            "timestamp": 1000.0,
            "meta": {"anchor_type": "handoff"},
        }
        entry = Entry.from_dict(d)
        assert isinstance(entry, Anchor)
        assert entry.anchor_type == "handoff"

    def test_from_dict_bridges_topic_initial_to_topic_start(self):
        from agentkit.tape.anchor import Anchor

        d = {
            "id": "old-2",
            "kind": "anchor",
            "payload": {},
            "timestamp": 1000.0,
            "meta": {"anchor_type": "topic_initial"},
        }
        entry = Entry.from_dict(d)
        assert isinstance(entry, Anchor)
        assert entry.anchor_type == "topic_start"
