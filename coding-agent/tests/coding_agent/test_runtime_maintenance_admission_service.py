from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from coding_agent.runs import RuntimeMaintenanceAdmissionService


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
    calls: list[str] | None = None,
) -> RuntimeMaintenanceAdmissionService:
    current_session = session or FakeSession()
    current_lock = lock or asyncio.Lock()

    async def assert_owner(session_id: str) -> None:
        if calls is not None:
            calls.append(f"owner:{session_id}")

    async def load_session(session_id: str) -> FakeSession:
        if calls is not None:
            calls.append(f"load:{session_id}")
        return current_session

    return RuntimeMaintenanceAdmissionService(
        turn_lock_for=lambda session_id: current_lock,
        assert_owner=assert_owner,
        load_session=load_session,
    )


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
async def test_run_exclusive_rejects_active_lock_without_owner_or_load() -> None:
    lock = asyncio.Lock()
    await lock.acquire()
    calls: list[str] = []

    async def body(session: object) -> None:
        del session
        calls.append("body")

    try:
        with pytest.raises(RuntimeError, match="turn already in progress"):
            await _service(lock=lock, calls=calls).run_exclusive("session-1", body)
    finally:
        lock.release()

    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session",
    [
        FakeSession(turn_in_progress=True),
        FakeSession(task=FakeTask(is_done=False)),
    ],
)
async def test_run_exclusive_rejects_active_session_before_body(
    session: FakeSession,
) -> None:
    calls: list[str] = []

    async def body(admitted_session: object) -> None:
        del admitted_session
        calls.append("body")

    with pytest.raises(RuntimeError, match="turn already in progress"):
        await _service(session=session, calls=calls).run_exclusive(
            "session-1",
            body,
        )

    assert calls == ["owner:session-1", "load:session-1"]


@pytest.mark.asyncio
async def test_run_exclusive_allows_completed_task() -> None:
    session = FakeSession(task=FakeTask(is_done=True))

    async def body(admitted_session: object) -> FakeSession:
        assert admitted_session is session
        return session

    admitted = await _service(session=session).run_exclusive("session-1", body)

    assert admitted is session
