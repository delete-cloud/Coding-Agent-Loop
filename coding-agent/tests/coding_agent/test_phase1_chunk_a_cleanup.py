from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"


def test_no_legacy_registry_or_subagent_imports_remain() -> None:
    banned_patterns = [
        r"\bcoding_agent\.tools\.registry\b",
        r"\bcoding_agent\.agents\.subagent\b",
    ]

    offenders: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        text = path.read_text()
        for banned in banned_patterns:
            if re.search(banned, text):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}::{banned}")

    assert offenders == []


def test_legacy_chunk_a_files_removed() -> None:
    legacy_files = [
        SRC_ROOT / "coding_agent" / "tools" / "search.py",
        SRC_ROOT / "coding_agent" / "tools" / "registry.py",
        SRC_ROOT / "coding_agent" / "agents" / "subagent.py",
    ]

    existing = [
        str(path.relative_to(PROJECT_ROOT)) for path in legacy_files if path.exists()
    ]

    assert existing == []


def test_http_server_legacy_session_shims_removed() -> None:
    http_server_path = SRC_ROOT / "coding_agent" / "ui" / "http_server.py"
    text = http_server_path.read_text()

    banned_patterns = [
        r"\bSessionState\s*=\s*Session\b",
        r"\bsessions\s*=\s*_SessionStoreView\(\)\b",
        r"\bsession\.pending_approval\b",
        r"\bsession\.approval_event\b",
        r"\bsession\.approval_response\b",
        r"\bsession\.event_queues\b",
    ]

    offenders = [pattern for pattern in banned_patterns if re.search(pattern, text)]

    assert offenders == []
