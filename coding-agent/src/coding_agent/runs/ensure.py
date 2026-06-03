from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol


class RuntimeEnsureSession(Protocol):
    id: str
    runtime_ctx: object | None
    runtime_adapter: object | None
    tape_id: str | None

    def attach_runtime_binding(
        self,
        *,
        pipeline: object,
        ctx: object,
        adapter: object,
    ) -> None: ...


RuntimeEnsureBuilder = Callable[
    [RuntimeEnsureSession],
    Awaitable[tuple[object, object, object]],
]
RuntimeEnsurePersister = Callable[[RuntimeEnsureSession], Awaitable[None]]
RuntimeEnsureOwnerAsserter = Callable[[str], Awaitable[None]]
RuntimeEnsureSessionLoader = Callable[[str], Awaitable[RuntimeEnsureSession]]


@dataclass(frozen=True)
class RuntimeEnsureService:
    async def ensure_runtime(
        self,
        session: RuntimeEnsureSession,
        *,
        build_runtime: RuntimeEnsureBuilder,
        persist_session: RuntimeEnsurePersister,
    ) -> object:
        if session.runtime_ctx is not None and session.runtime_adapter is not None:
            return session.runtime_ctx

        pipeline, ctx, adapter = await build_runtime(session)
        session.attach_runtime_binding(
            pipeline=pipeline,
            ctx=ctx,
            adapter=adapter,
        )
        session.tape_id = ctx.tape.tape_id
        await persist_session(session)
        return ctx


@dataclass(frozen=True)
class RuntimeEnsureOrchestrationService:
    ensure_service: RuntimeEnsureService
    assert_owner: RuntimeEnsureOwnerAsserter
    load_session: RuntimeEnsureSessionLoader
    build_runtime: RuntimeEnsureBuilder
    persist_session: RuntimeEnsurePersister

    async def ensure_session_runtime(self, session_id: str) -> object:
        await self.assert_owner(session_id)
        session = await self.load_session(session_id)
        return await self.ensure_service.ensure_runtime(
            session,
            build_runtime=self.build_runtime,
            persist_session=self.persist_session,
        )


__all__ = [
    "RuntimeEnsureBuilder",
    "RuntimeEnsureOrchestrationService",
    "RuntimeEnsureOwnerAsserter",
    "RuntimeEnsurePersister",
    "RuntimeEnsureService",
    "RuntimeEnsureSession",
    "RuntimeEnsureSessionLoader",
]
