from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from coding_agent.environment.execution_binding import (
    CloudWorkspaceBinding,
    LocalExecutionBinding,
)
from coding_agent.runs import RuntimeWorkspaceExportService


@dataclass
class FakeTask:
    is_done: bool = False

    def done(self) -> bool:
        return self.is_done


@dataclass
class FakeSession:
    execution_binding: object
    turn_in_progress: bool = False
    task: FakeTask | None = None


def _cloud_binding() -> CloudWorkspaceBinding:
    return CloudWorkspaceBinding(
        workspace_url="docker://agent-ws-export/workspace",
        workspace_id="ws-export",
    )


def _service(
    *,
    session: FakeSession | None = None,
    lock: asyncio.Lock | None = None,
    calls: list[str] | None = None,
) -> RuntimeWorkspaceExportService:
    current_session = session or FakeSession(execution_binding=_cloud_binding())
    current_lock = lock or asyncio.Lock()

    async def assert_owner(session_id: str) -> None:
        if calls is not None:
            calls.append(f"owner:{session_id}")

    async def load_session(session_id: str) -> FakeSession:
        if calls is not None:
            calls.append(f"load:{session_id}")
        return current_session

    def begin_export(session_id: str) -> None:
        if calls is not None:
            calls.append(f"begin:{session_id}")

    def end_export(session_id: str) -> None:
        if calls is not None:
            calls.append(f"end:{session_id}")

    return RuntimeWorkspaceExportService(
        turn_lock_for=lambda session_id: current_lock,
        assert_owner=assert_owner,
        load_session=load_session,
        begin_export=begin_export,
        end_export=end_export,
    )


@pytest.mark.asyncio
async def test_export_archive_runs_export_and_revalidates_owner() -> None:
    calls: list[str] = []

    result = await _service(calls=calls).export_archive(
        "session-1",
        lambda binding: f"{binding.workspace_id}-archive",
    )

    assert result == "ws-export-archive"
    assert calls == [
        "owner:session-1",
        "load:session-1",
        "begin:session-1",
        "owner:session-1",
        "end:session-1",
    ]


@pytest.mark.asyncio
async def test_export_archive_rejects_active_lock_without_loading_session() -> None:
    lock = asyncio.Lock()
    await lock.acquire()
    calls: list[str] = []

    try:
        with pytest.raises(RuntimeError, match="turn already in progress"):
            await _service(lock=lock, calls=calls).export_archive(
                "session-1",
                lambda binding: binding.workspace_id,
            )
    finally:
        lock.release()

    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session",
    [
        FakeSession(execution_binding=_cloud_binding(), turn_in_progress=True),
        FakeSession(execution_binding=_cloud_binding(), task=FakeTask(is_done=False)),
    ],
)
async def test_export_archive_rejects_active_session_before_begin(
    session: FakeSession,
) -> None:
    calls: list[str] = []

    with pytest.raises(RuntimeError, match="turn already in progress"):
        await _service(session=session, calls=calls).export_archive(
            "session-1",
            lambda binding: binding.workspace_id,
        )

    assert calls == ["owner:session-1", "load:session-1"]


@pytest.mark.asyncio
async def test_export_archive_rejects_non_cloud_session_before_begin() -> None:
    calls: list[str] = []
    session = FakeSession(
        execution_binding=LocalExecutionBinding(workspace_root="/tmp/repo"),
    )

    with pytest.raises(ValueError, match="Workspace export requires cloud session"):
        await _service(session=session, calls=calls).export_archive(
            "session-1",
            lambda binding: binding.workspace_id,
        )

    assert calls == ["owner:session-1", "load:session-1"]


@pytest.mark.asyncio
async def test_export_archive_ends_export_when_exporter_fails() -> None:
    calls: list[str] = []

    def fail_export(binding: CloudWorkspaceBinding) -> str:
        del binding
        raise RuntimeError("export failed")

    with pytest.raises(RuntimeError, match="export failed"):
        await _service(calls=calls).export_archive("session-1", fail_export)

    assert calls == [
        "owner:session-1",
        "load:session-1",
        "begin:session-1",
        "end:session-1",
    ]
