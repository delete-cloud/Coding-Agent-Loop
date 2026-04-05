from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"


def test_no_legacy_registry_or_subagent_imports_remain() -> None:
    banned_imports = [
        "coding_agent.tools.registry",
        "coding_agent.agents.subagent",
    ]

    offenders: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        text = path.read_text()
        for banned in banned_imports:
            if banned in text:
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}::{banned}")

    assert offenders == []


def test_legacy_chunk_a_files_removed() -> None:
    legacy_files = [
        SRC_ROOT / "coding_agent" / "tools" / "search.py",
        SRC_ROOT / "coding_agent" / "tools" / "registry.py",
        SRC_ROOT / "coding_agent" / "tools" / "subagent.py",
        SRC_ROOT / "coding_agent" / "agents" / "subagent.py",
    ]

    existing = [
        str(path.relative_to(PROJECT_ROOT)) for path in legacy_files if path.exists()
    ]

    assert existing == []
