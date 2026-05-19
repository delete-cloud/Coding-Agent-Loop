from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

from agentkit.tape.anchor import Anchor
from agentkit.tape.tape import Tape
from coding_agent.plugins.storage import JSONLTapeStore


def test_packaging_metadata_exposes_cli_and_runtime_packages() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["scripts"]["coding-agent"] == (
        "coding_agent.__main__:main"
    )
    assert set(pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]) == {
        "src/agentkit",
        "src/coding_agent",
    }


@pytest.mark.asyncio
async def test_jsonl_store_loads_legacy_and_current_anchor_entries(
    tmp_path: Path,
) -> None:
    store = JSONLTapeStore(tmp_path)
    entries = [
        {
            "id": "m1",
            "kind": "message",
            "payload": {"role": "user", "content": "before"},
            "timestamp": 1.0,
        },
        {
            "id": "legacy-handoff",
            "kind": "anchor",
            "payload": {"content": "legacy summary"},
            "timestamp": 2.0,
            "meta": {"is_handoff": True},
        },
        {
            "id": "m2",
            "kind": "message",
            "payload": {"role": "assistant", "content": "middle"},
            "timestamp": 3.0,
        },
        {
            "id": "current-handoff",
            "kind": "anchor",
            "payload": {"content": "current summary"},
            "timestamp": 4.0,
            "anchor_type": "handoff",
            "source_ids": ["m2"],
        },
        {
            "id": "m3",
            "kind": "message",
            "payload": {"role": "user", "content": "after"},
            "timestamp": 5.0,
        },
    ]

    await store.save("release-tape", entries[:2])
    await store.save("release-tape", entries[2:])

    raw_entries = await store.load("release-tape")
    tape = Tape.from_list(raw_entries, tape_id="release-tape")

    assert raw_entries == entries
    assert tape.tape_id == "release-tape"
    assert tape.window_start == 3
    legacy_anchor = tape[1]
    assert isinstance(legacy_anchor, Anchor)
    assert legacy_anchor.id == "legacy-handoff"
    assert legacy_anchor.is_handoff is True
    anchor = tape[3]
    assert isinstance(anchor, Anchor)
    assert anchor.id == "current-handoff"
    assert anchor.source_ids == ("m2",)
    assert [entry.id for entry in tape.windowed_entries()] == ["current-handoff", "m3"]


def test_tape_jsonl_append_after_load_writes_only_new_entries(tmp_path: Path) -> None:
    path = tmp_path / "release-tape.jsonl"
    tape = Tape(tape_id="release-tape")
    tape.append(
        Anchor(
            id="handoff-1",
            payload={"content": "summary"},
            timestamp=1.0,
            anchor_type="handoff",
        )
    )
    tape.save_jsonl(path)

    restored = Tape.load_jsonl(path, tape_id="release-tape")
    restored.append(
        Anchor(
            id="topic-1",
            payload={"content": "topic closed"},
            timestamp=2.0,
            anchor_type="topic_end",
        )
    )
    restored.save_jsonl(path)

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    roundtripped = Tape.load_jsonl(path, tape_id="release-tape")

    assert len(lines) == 2
    assert len(roundtripped) == 2
    assert roundtripped.window_start == 0
    assert isinstance(roundtripped[0], Anchor)
    assert isinstance(roundtripped[1], Anchor)
    assert roundtripped[1].fold_boundary is True
