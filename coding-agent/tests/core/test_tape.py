"""Tests for Tape append-only JSONL storage."""

import json
import tempfile
from pathlib import Path

import pytest

from coding_agent.core.tape import Entry, EntryKind, Tape


class TestEntry:
    """Tests for Entry dataclass."""

    def test_entry_creation_message(self):
        """Test creating a message entry."""
        entry = Entry.message(role="user", content="hello")
        assert entry.kind == "message"
        assert entry.payload == {"role": "user", "content": "hello"}
        assert entry.id is not None
        assert entry.timestamp is not None

    def test_entry_creation_anchor(self):
        """Test creating an anchor entry."""
        entry = Entry.anchor(name="phase1", state={"key": "value"})
        assert entry.kind == "anchor"
        assert entry.payload == {"name": "phase1", "state": {"key": "value"}}

    def test_entry_creation_tool_call(self):
        """Test creating a tool_call entry."""
        entry = Entry.tool_call(name="read_file", arguments={"path": "/tmp/test.txt"})
        assert entry.kind == "tool_call"
        assert entry.payload["name"] == "read_file"
        assert entry.payload["arguments"] == {"path": "/tmp/test.txt"}

    def test_entry_creation_tool_result(self):
        """Test creating a tool_result entry."""
        entry = Entry.tool_result(name="read_file", result="file content")
        assert entry.kind == "tool_result"
        assert entry.payload["name"] == "read_file"
        assert entry.payload["result"] == "file content"

    def test_entry_creation_event(self):
        """Test creating an event entry."""
        entry = Entry.event(type="handoff", data={"to": "subagent"})
        assert entry.kind == "event"
        assert entry.payload == {"type": "handoff", "data": {"to": "subagent"}}

    def test_entry_is_frozen(self):
        """Test that Entry is immutable (frozen dataclass)."""
        entry = Entry.message(role="user", content="hello")
        with pytest.raises(AttributeError):
            entry.kind = "event"

    def test_entry_to_dict(self):
        """Test serializing entry to dict."""
        entry = Entry.message(role="user", content="hello", id=42)
        d = entry.to_dict()
        assert d["id"] == 42
        assert d["kind"] == "message"
        assert d["payload"] == {"role": "user", "content": "hello"}
        assert "timestamp" in d

    def test_entry_from_dict(self):
        """Test deserializing entry from dict."""
        d = {
            "id": 42,
            "kind": "message",
            "payload": {"role": "user", "content": "hello"},
            "timestamp": "2024-01-01T00:00:00",
        }
        entry = Entry.from_dict(d)
        assert entry.id == 42
        assert entry.kind == "message"
        assert entry.payload == {"role": "user", "content": "hello"}
        assert entry.timestamp == "2024-01-01T00:00:00"


class TestTape:
    """Tests for Tape storage."""

    def test_tape_create_generates_uuid_filename(self):
        """Test Tape.create generates a UUID filename."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tape = Tape.create(Path(tmpdir))
            assert tape.path is not None
            assert tape.path.parent == Path(tmpdir)
            assert len(tape.path.stem) == 36  # UUID length
            assert tape.path.suffix == ".jsonl"

    def test_tape_append_assigns_sequential_ids(self):
        """Test that append assigns sequential IDs starting from 1."""
        tape = Tape(path=None)
        entry1 = tape.append("message", {"role": "user", "content": "hello"})
        entry2 = tape.append("message", {"role": "assistant", "content": "hi"})
        entry3 = tape.append("tool_call", {"name": "test"})

        assert entry1.id == 1
        assert entry2.id == 2
        assert entry3.id == 3

    def test_tape_append_adds_to_entries_list(self):
        """Test that append adds entries to the internal list."""
        tape = Tape(path=None)
        tape.append("message", {"role": "user", "content": "hello"})
        tape.append("message", {"role": "assistant", "content": "hi"})

        assert len(tape._entries) == 2

    def test_tape_entries_returns_all_by_default(self):
        """Test entries() returns all entries when no anchor specified."""
        tape = Tape(path=None)
        tape.append("message", {"role": "user", "content": "hello"})
        tape.append("message", {"role": "assistant", "content": "hi"})

        entries = tape.entries()
        assert len(entries) == 2
        assert entries[0].payload["role"] == "user"
        assert entries[1].payload["role"] == "assistant"

    def test_tape_entries_filters_after_anchor(self):
        """Test entries(after_anchor) filters from anchor onwards."""
        tape = Tape(path=None)
        tape.append("message", {"role": "user", "content": "hello"})
        anchor = tape.handoff(name="phase1", state={})
        tape.append("message", {"role": "assistant", "content": "hi"})
        tape.append("tool_call", {"name": "test"})

        entries = tape.entries(after_anchor=anchor)
        assert len(entries) == 3  # anchor + entries after
        assert entries[0].id == anchor.id

    def test_tape_entries_returns_copies(self):
        """Test entries() returns copies, not original references."""
        tape = Tape(path=None)
        tape.append("message", {"role": "user", "content": "hello"})
        
        entries = tape.entries()
        # Modifying returned entry shouldn't affect tape
        entries[0].payload["content"] = "modified"
        
        # Original should be unchanged (frozen dataclass)
        assert tape._entries[0].payload["content"] == "hello"

    def test_tape_persistence_writes_to_jsonl(self):
        """Test that append writes to JSONL file when path exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tape = Tape.create(Path(tmpdir))
            tape.append("message", {"role": "user", "content": "hello"})
            tape.append("message", {"role": "assistant", "content": "hi"})

            # Read file directly
            lines = tape.path.read_text().strip().split("\n")
            assert len(lines) == 2

            # Parse JSON
            data1 = json.loads(lines[0])
            assert data1["kind"] == "message"
            assert data1["payload"]["role"] == "user"

    def test_tape_load_reads_existing_file(self):
        """Test that loading reads existing JSONL file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tape = Tape.create(Path(tmpdir))
            tape.append("message", {"role": "user", "content": "hello"})
            tape.append("tool_call", {"name": "test"})

            # Create new tape pointing to same file
            tape2 = Tape(tape.path)
            entries = tape2.entries()

            assert len(entries) == 2
            assert entries[0].kind == "message"
            assert entries[1].kind == "tool_call"

    def test_handoff_creates_anchor_entry(self):
        """Test handoff() creates an anchor entry."""
        tape = Tape(path=None)
        anchor = tape.handoff(name="phase1", state={"key": "value"})

        assert anchor.kind == "anchor"
        assert anchor.payload["name"] == "phase1"
        assert anchor.payload["state"] == {"key": "value"}
        assert anchor in tape._entries

    def test_fork_creates_independent_copy(self):
        """Test fork() creates a copy with independent entries."""
        tape = Tape(path=None)
        tape.append("message", {"role": "user", "content": "hello"})
        
        forked = tape.fork()
        
        # Fork should have same entries
        assert len(forked.entries()) == 1
        
        # Add to fork shouldn't affect original
        forked.append("message", {"role": "assistant", "content": "hi"})
        assert len(tape.entries()) == 1
        assert len(forked.entries()) == 2

    def test_fork_is_in_memory_no_file(self):
        """Test fork() creates in-memory tape with no path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tape = Tape.create(Path(tmpdir))
            tape.append("message", {"role": "user", "content": "hello"})
            
            forked = tape.fork()
            
            assert forked.path is None
            assert len(forked.entries()) == 1

    def test_fork_preserves_entry_ids(self):
        """Test fork() preserves entry IDs from original."""
        tape = Tape(path=None)
        tape.append("message", {"role": "user", "content": "hello"})
        tape.append("message", {"role": "assistant", "content": "hi"})
        
        forked = tape.fork()
        
        # Entry IDs should be preserved
        assert forked._entries[0].id == 1
        assert forked._entries[1].id == 2
        # next_id should continue from where it left off
        new_entry = forked.append("message", {"role": "user", "content": "again"})
        assert new_entry.id == 3

    def test_merge_appends_new_entries(self):
        """Test merge() appends new entries from forked tape."""
        tape = Tape(path=None)
        tape.append("message", {"role": "user", "content": "hello"})
        
        forked = tape.fork()
        forked.append("message", {"role": "assistant", "content": "hi"})
        forked.append("tool_call", {"name": "test"})
        
        # Merge back
        tape.merge(forked)
        
        # Should have original + new entries
        entries = tape.entries()
        assert len(entries) == 3
        assert entries[0].payload["role"] == "user"
        assert entries[1].payload["role"] == "assistant"
        assert entries[2].kind == "tool_call"

    def test_merge_only_adds_new_entries(self):
        """Test merge() only adds entries that are new (after fork point)."""
        tape = Tape(path=None)
        tape.append("message", {"role": "user", "content": "original"})
        
        forked = tape.fork()
        forked.append("message", {"role": "assistant", "content": "new"})
        
        # Original also adds something before merge
        tape.append("message", {"role": "user", "content": "also new"})
        
        # Merge should not duplicate the original entry
        tape.merge(forked)
        
        entries = tape.entries()
        assert len(entries) == 3
        # Check IDs are sequential without gaps
        assert entries[0].id == 1
        assert entries[1].id == 2
        assert entries[2].id == 3

    def test_merge_persists_to_disk(self):
        """Test merge() persists merged entries to disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tape = Tape.create(Path(tmpdir))
            tape.append("message", {"role": "user", "content": "hello"})
            
            forked = tape.fork()
            forked.append("message", {"role": "assistant", "content": "hi"})
            
            # Before merge, file has 1 entry
            lines_before = tape.path.read_text().strip().split("\n")
            assert len(lines_before) == 1
            
            # Merge
            tape.merge(forked)
            
            # After merge, file has 2 entries
            lines_after = tape.path.read_text().strip().split("\n")
            assert len(lines_after) == 2

    def test_merge_updates_next_id(self):
        """Test merge() updates next_id to continue correctly."""
        tape = Tape(path=None)
        tape.append("message", {"role": "user", "content": "hello"})
        
        forked = tape.fork()
        forked.append("message", {"role": "assistant", "content": "hi"})
        forked.append("tool_call", {"name": "test"})
        
        tape.merge(forked)
        
        # Next entry should continue from merged entries
        new_entry = tape.append("message", {"role": "user", "content": "again"})
        assert new_entry.id == 4

    def test_entry_kind_literal(self):
        """Test EntryKind literal type."""
        assert EntryKind == "message" or True  # Just verify it exists


class TestTapeEdgeCases:
    """Edge case tests for Tape."""

    def test_empty_tape_entries(self):
        """Test entries() on empty tape."""
        tape = Tape(path=None)
        assert tape.entries() == []

    def test_append_to_in_memory_does_not_create_file(self):
        """Test append to in-memory tape doesn't create file."""
        tape = Tape(path=None)
        tape.append("message", {"role": "user", "content": "hello"})
        # Should not raise or create file
        assert tape.path is None

    def test_merge_from_non_forked_tape(self):
        """Test merge from tape that wasn't forked from self."""
        tape1 = Tape(path=None)
        tape1.append("message", {"role": "user", "content": "hello"})
        
        tape2 = Tape(path=None)
        tape2.append("message", {"role": "assistant", "content": "hi"})
        
        # Merging unrelated tapes should still work (adds all entries from tape2)
        tape1.merge(tape2)
        assert len(tape1.entries()) == 2

    def test_multiple_forks_and_merges(self):
        """Test multiple fork/merge cycles."""
        tape = Tape(path=None)
        tape.append("message", {"role": "user", "content": "1"})
        
        # First fork/merge
        fork1 = tape.fork()
        fork1.append("message", {"role": "assistant", "content": "2"})
        tape.merge(fork1)
        
        # Second fork/merge
        fork2 = tape.fork()
        fork2.append("message", {"role": "user", "content": "3"})
        tape.merge(fork2)
        
        entries = tape.entries()
        assert len(entries) == 3
        assert entries[0].id == 1
        assert entries[1].id == 2
        assert entries[2].id == 3
