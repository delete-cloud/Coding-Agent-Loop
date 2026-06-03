from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from coding_agent.runs import RuntimeTurnAdmissionService


@dataclass
class FakeTask:
    is_done: bool = False

    def cancel(self) -> None:
        pass

    def done(self) -> bool:
        return self.is_done

    def __await__(self):
        if False:
            yield None
        return None


@dataclass
class FakeSession:
    turn_in_progress: bool = False
    task: FakeTask | None = None


def _service(
    *,
    session: FakeSession | None = None,
    lock: asyncio.Lock | None = None,
    export_in_progress: bool = False,
    calls: list[str] | None = None,
) -> RuntimeTurnAdmissionService:
    current_session = session or FakeSession()
    current_lock = lock or asyncio.Lock()

    async def assert_owner(session_id: str) -> None:
        if calls is not None:
            calls.append(f"owner:{session_id}")

    async def load_session(session_id: str) -> FakeSession:
        if calls is not None:
            calls.append(f"load:{session_id}")
        return current_session

    return RuntimeTurnAdmissionService(
        turn_lock_for=lambda session_id: current_lock,
        workspace_export_in_progress=lambda session_id: export_in_progress,
        assert_owner=assert_owner,
        load_session=load_session,
    )


@pytest.mark.asyncio
async def test_prepare_session_turn_returns_idle_owned_session() -> None:
    session = FakeSession()
    calls: list[str] = []

    admitted = await _service(session=session, calls=calls).prepare_session_turn(
        "session-1"
    )

    assert admitted is session
    assert calls == ["load:session-1", "owner:session-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session",
    [
        FakeSession(turn_in_progress=True),
        FakeSession(task=FakeTask(is_done=False)),
    ],
)
async def test_prepare_session_turn_rejects_active_session(
    session: FakeSession,
) -> None:
    with pytest.raises(RuntimeError, match="turn already in progress"):
        await _service(session=session).prepare_session_turn("session-1")


@pytest.mark.asyncio
async def test_prepare_session_turn_rejects_active_lock() -> None:
    lock = asyncio.Lock()
    await lock.acquire()

    try:
        with pytest.raises(RuntimeError, match="turn already in progress"):
            await _service(lock=lock).prepare_session_turn("session-1")
    finally:
        lock.release()


@pytest.mark.asyncio
async def test_run_exclusive_runs_body_under_turn_lock() -> None:
    lock = asyncio.Lock()
    calls: list[str] = []

    async def body(session: object) -> str:
        calls.append(f"body_locked:{lock.locked()}")
        assert isinstance(session, FakeSession)
        return "done"

    result = await _service(lock=lock, calls=calls).run_exclusive("session-1", body)

    assert result == "done"
    assert calls == ["owner:session-1", "load:session-1", "body_locked:True"]
    assert lock.locked() is False


@pytest.mark.asyncio
async def test_run_exclusive_rejects_export_without_loading_session() -> None:
    calls: list[str] = []

    async def body(session: object) -> None:
        del session
        calls.append("body")

    with pytest.raises(RuntimeError, match="turn already in progress"):
        await _service(export_in_progress=True, calls=calls).run_exclusive(
            "session-1",
            body,
        )

    assert calls == []
