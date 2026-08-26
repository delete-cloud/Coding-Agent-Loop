from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from coding_agent.server.session_manager import Session, SessionManager
from coding_agent.server.stores.session_owner_store import SQLiteSessionOwnerStore
from coding_agent.stores.local import local_sqlite_storage_config


@pytest.fixture
async def fresh_manager(tmp_path) -> AsyncIterator[SessionManager]:
    manager = SessionManager(
        storage_config=local_sqlite_storage_config(tmp_path),
        owner_store=SQLiteSessionOwnerStore(tmp_path / "local.sqlite3"),
        owner_id="fresh-admission-owner",
        fencing_token=1,
    )
    try:
        yield manager
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_fresh_session_admission_initializes_runtime_tape(
    fresh_manager: SessionManager,
) -> None:
    manager = fresh_manager
    now = datetime.now(UTC)
    session = Session(id="session-fresh", created_at=now, last_activity=now)
    manager._session_cache[session.id] = session
    await manager._acquire_owner_for_session(session.id)
    store = manager._authoritative_store()
    assert store is not None
    await store.save_session(
        manager._owner_authorities[session.id], session.to_store_data()
    )

    admission = await manager.admit_chat_command(
        session.id, prompt="hello", command_id="command-fresh"
    )

    assert session.tape_id is not None
    assert session.tape_id
    snapshot = await store.snapshot_chat_events(session.id, None, 10)
    assert [event.source_event_id for event in snapshot.events] == [
        "session-fresh:chat-command:command-fresh"
    ]
    assert snapshot.events[0].run_id == admission.run_id
