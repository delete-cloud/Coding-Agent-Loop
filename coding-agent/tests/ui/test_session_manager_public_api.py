from __future__ import annotations

from datetime import datetime

from coding_agent.approval.store import ApprovalStore
from coding_agent.ui.session_manager import Session, SessionManager


def test_register_session_uses_public_api() -> None:
    manager = SessionManager()
    approval_store = ApprovalStore()
    session = Session(
        id="test-session",
        created_at=datetime.now(),
        last_activity=datetime.now(),
        approval_store=approval_store,
    )

    manager.register_session(session)

    assert manager.has_session("test-session")
    assert manager.get_session("test-session") is session


def test_clear_sessions_uses_public_api() -> None:
    manager = SessionManager()
    session = Session(
        id="test-session",
        created_at=datetime.now(),
        last_activity=datetime.now(),
        approval_store=ApprovalStore(),
    )
    manager.register_session(session)

    manager.clear_sessions()

    assert manager.list_sessions() == []
