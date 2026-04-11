import pytest
from agentkit.tape.view import TapeView
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry


class TestTapeView:
    def test_from_tape_no_windowing(self):
        """When window_start is 0, view contains all entries."""
        tape = Tape()
        entries = [Entry(kind="message", payload={"content": str(i)}) for i in range(5)]
        for e in entries:
            tape.append(e)
        view = TapeView.from_tape(tape)
        assert len(view) == 5
        assert view.entries == entries
        assert view.source_tape_id == tape.tape_id
        assert view.window_start == 0

    def test_from_tape_with_windowing(self):
        """When tape has been handed off, view contains only windowed entries."""
        tape = Tape()
        for i in range(10):
            tape.append(Entry(kind="message", payload={"content": str(i)}))
        anchor = Entry(
            kind="anchor",
            payload={"content": "summary"},
            meta={"is_handoff": True},
        )
        tape.handoff(anchor)
        tape.append(Entry(kind="message", payload={"content": "after"}))

        view = TapeView.from_tape(tape)
        assert len(view) == 2  # anchor + "after"
        assert view.entries[0] is anchor
        assert view.window_start == tape.window_start

    def test_from_tape_moves_handoff_anchor_before_recent_entries(self):
        tape = Tape()
        for i in range(6):
            tape.append(Entry(kind="message", payload={"content": f"old-{i}"}))
        anchor = Entry(
            kind="anchor",
            payload={"content": "summary"},
            meta={"is_handoff": True},
        )
        tape.handoff(anchor, window_start=3)
        tape.append(Entry(kind="message", payload={"content": "new-0"}))
        tape.append(Entry(kind="message", payload={"content": "new-1"}))

        view = TapeView.from_tape(tape)

        assert view.entries[0] is anchor
        assert [entry.payload.get("content") for entry in view.entries[1:]] == [
            "old-3",
            "old-4",
            "old-5",
            "new-0",
            "new-1",
        ]

    def test_from_tape_tracks_anchor_ids(self):
        tape = Tape()
        tape.append(Entry(kind="message", payload={"content": "old"}))
        anchor = Entry(
            kind="anchor",
            payload={"content": "summary"},
            meta={"is_handoff": True},
        )
        tape.handoff(anchor)
        tape.append(Entry(kind="message", payload={"content": "new"}))

        view = TapeView.from_tape(tape)
        assert anchor.id in view.anchor_ids

    def test_full_ignores_windowing(self):
        """TapeView.full() returns all entries regardless of window_start."""
        tape = Tape()
        for i in range(5):
            tape.append(Entry(kind="message", payload={"content": str(i)}))
        anchor = Entry(
            kind="anchor",
            payload={"content": "summary"},
            meta={"is_handoff": True},
        )
        tape.handoff(anchor)

        view = TapeView.full(tape)
        assert len(view) == 6  # all 5 + anchor
        assert view.window_start == 0

    def test_iterable(self):
        tape = Tape()
        entries = [Entry(kind="message", payload={"content": str(i)}) for i in range(3)]
        for e in entries:
            tape.append(e)
        view = TapeView.from_tape(tape)
        assert list(view) == entries

    def test_empty_tape(self):
        tape = Tape()
        view = TapeView.from_tape(tape)
        assert len(view) == 0
        assert view.entries == []
        assert view.anchor_ids == ()
