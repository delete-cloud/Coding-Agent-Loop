from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from .session_runtime import RuntimeBindingSnapshot


logger = logging.getLogger(__name__)


class RuntimeReplacementSession(Protocol):
    id: str
    provider: object | None
    model_name: str | None
    tape_id: str | None

    def runtime_binding_snapshot(self) -> RuntimeBindingSnapshot: ...

    def attach_runtime_binding(
        self,
        *,
        pipeline: object,
        ctx: object,
        adapter: object,
    ) -> None: ...

    def restore_runtime_binding(self, snapshot: RuntimeBindingSnapshot) -> None: ...


RuntimeReplacementPersister = Callable[
    [RuntimeReplacementSession],
    Awaitable[None],
]
RuntimeAdapterCloseHook = Callable[[object | None], Awaitable[None]]


class RuntimeReplacementBuilder(Protocol):
    def __call__(
        self,
        session: RuntimeReplacementSession,
        *,
        model_name: str,
    ) -> Awaitable[tuple[object, object, object]]: ...


@dataclass(frozen=True)
class RuntimeReplacementService:
    close_runtime_adapter: RuntimeAdapterCloseHook

    async def replace_runtime_config(
        self,
        session: RuntimeReplacementSession,
        *,
        model_name: str,
        build_runtime: RuntimeReplacementBuilder,
        persist_session: RuntimeReplacementPersister,
    ) -> RuntimeReplacementSession:
        old_provider = session.provider
        old_model_name = session.model_name
        old_tape_id = session.tape_id
        old_runtime_binding = session.runtime_binding_snapshot()

        pipeline, ctx, adapter = await build_runtime(session, model_name=model_name)

        session.provider = None
        session.model_name = model_name
        session.attach_runtime_binding(
            pipeline=pipeline,
            ctx=ctx,
            adapter=adapter,
        )
        session.tape_id = ctx.tape.tape_id
        try:
            await persist_session(session)
        except Exception:
            session.provider = old_provider
            session.model_name = old_model_name
            session.restore_runtime_binding(old_runtime_binding)
            session.tape_id = old_tape_id
            await self.close_runtime_adapter(adapter)
            raise

        try:
            await self.close_runtime_adapter(old_runtime_binding.adapter)
        except Exception:
            logger.warning(
                "Failed to close previous runtime adapter for session %s",
                session.id,
                exc_info=True,
            )
        return session


__all__ = [
    "RuntimeReplacementBuilder",
    "RuntimeReplacementPersister",
    "RuntimeReplacementService",
    "RuntimeReplacementSession",
]
