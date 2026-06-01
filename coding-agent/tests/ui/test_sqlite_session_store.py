from __future__ import annotations

from coding_agent.server.stores.session_store import create_session_store


def test_sqlite_session_store_persists_sessions_across_instances(tmp_path) -> None:
    path = tmp_path / "sessions.sqlite3"
    first = create_session_store(backend="sqlite", file_path=path)

    first.save("session-1", {"id": "session-1", "status": "active"})
    first.save("session-2", {"id": "session-2", "status": "idle"})
    first.delete("session-2")

    second = create_session_store(backend="sqlite", file_path=path)

    assert second.check_health()
    assert second.count_sessions() == 1
    assert second.list_sessions() == ["session-1"]
    assert second.load("session-1") == {"id": "session-1", "status": "active"}
    assert second.load("session-2") is None
