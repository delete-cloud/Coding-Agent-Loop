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


__all__ = [
    "RuntimeEnsureBuilder",
    "RuntimeEnsurePersister",
    "RuntimeEnsureService",
    "RuntimeEnsureSession",
]
