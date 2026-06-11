from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from .session_runtime import RuntimeBindingSnapshot


logger = logging.getLogger(__name__)


class UnsetType:
    """Sentinel distinguishing "field not provided" from an explicit None.

    For base_url: UNSET keeps the session's current value, None resets it
    to the provider default, and a string sets it.
    """

    def __repr__(self) -> str:
        return "UNSET"


UNSET = UnsetType()


class RuntimeReplacementSession(Protocol):
    id: str
    provider: object | None
    provider_name: str | None
    model_name: str | None
    base_url: str | None
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
        provider_name: str | None = None,
        base_url: str | None | UnsetType = UNSET,
    ) -> Awaitable[tuple[object, object, object]]: ...


@dataclass(frozen=True)
class RuntimeReplacementService:
    close_runtime_adapter: RuntimeAdapterCloseHook

    async def replace_runtime_config(
        self,
        session: RuntimeReplacementSession,
        *,
        model_name: str,
        provider_name: str | None = None,
        base_url: str | None | UnsetType = UNSET,
        build_runtime: RuntimeReplacementBuilder,
        persist_session: RuntimeReplacementPersister,
    ) -> RuntimeReplacementSession:
        old_provider = session.provider
        old_provider_name = session.provider_name
        old_model_name = session.model_name
        old_base_url = session.base_url
        old_tape_id = session.tape_id
        old_runtime_binding = session.runtime_binding_snapshot()

        pipeline, ctx, adapter = await build_runtime(
            session,
            model_name=model_name,
            provider_name=provider_name,
            base_url=base_url,
        )

        session.provider = None
        session.model_name = model_name
        if provider_name is not None:
            session.provider_name = provider_name
        if not isinstance(base_url, UnsetType):
            session.base_url = base_url
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
            session.provider_name = old_provider_name
            session.model_name = old_model_name
            session.base_url = old_base_url
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
    "UNSET",
    "RuntimeReplacementBuilder",
    "RuntimeReplacementPersister",
    "RuntimeReplacementService",
    "RuntimeReplacementSession",
    "UnsetType",
]
