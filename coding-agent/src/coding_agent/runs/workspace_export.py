from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Protocol, TypeVar

from .target import CloudWorkspaceRef

T = TypeVar("T")


class RuntimeWorkspaceExportLock(Protocol):
    def locked(self) -> bool: ...

    async def __aenter__(self) -> object: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class RuntimeWorkspaceExportTask(Protocol):
    def done(self) -> bool: ...


class RuntimeWorkspaceExportSession(Protocol):
    default_run_target: object
    turn_in_progress: bool
    task: RuntimeWorkspaceExportTask | None


RuntimeWorkspaceExportLockProvider = Callable[[str], RuntimeWorkspaceExportLock]
RuntimeWorkspaceExportOwnerAsserter = Callable[[str], Awaitable[None]]
RuntimeWorkspaceExportSessionLoader = Callable[
    [str],
    Awaitable[RuntimeWorkspaceExportSession],
]
RuntimeWorkspaceExportBegin = Callable[[str], None]
RuntimeWorkspaceExportEnd = Callable[[str], None]
RuntimeWorkspaceArchiveExporter = Callable[[CloudWorkspaceRef], T]


@dataclass(frozen=True)
class RuntimeWorkspaceExportService:
    turn_lock_for: RuntimeWorkspaceExportLockProvider
    assert_owner: RuntimeWorkspaceExportOwnerAsserter
    load_session: RuntimeWorkspaceExportSessionLoader
    begin_export: RuntimeWorkspaceExportBegin
    end_export: RuntimeWorkspaceExportEnd

    async def export_archive(
        self,
        session_id: str,
        export_archive: RuntimeWorkspaceArchiveExporter[T],
    ) -> T:
        turn_lock = self.turn_lock_for(session_id)
        if turn_lock.locked():
            raise RuntimeError("turn already in progress")

        await self.assert_owner(session_id)
        session = await self.load_session(session_id)
        if session.turn_in_progress or (
            session.task is not None and not session.task.done()
        ):
            raise RuntimeError("turn already in progress")
        target = session.default_run_target
        if not hasattr(target, "workspace") or not isinstance(
            target.workspace,
            CloudWorkspaceRef,
        ):
            raise ValueError("Workspace export requires cloud session")

        self.begin_export(session_id)
        try:
            result = await asyncio.to_thread(
                export_archive,
                target.workspace,
            )
            await self.assert_owner(session_id)
            return result
        finally:
            self.end_export(session_id)


__all__ = [
    "RuntimeWorkspaceArchiveExporter",
    "RuntimeWorkspaceExportBegin",
    "RuntimeWorkspaceExportEnd",
    "RuntimeWorkspaceExportLock",
    "RuntimeWorkspaceExportLockProvider",
    "RuntimeWorkspaceExportOwnerAsserter",
    "RuntimeWorkspaceExportService",
    "RuntimeWorkspaceExportSession",
    "RuntimeWorkspaceExportSessionLoader",
    "RuntimeWorkspaceExportTask",
]
