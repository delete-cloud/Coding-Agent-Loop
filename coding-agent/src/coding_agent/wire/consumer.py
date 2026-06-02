from __future__ import annotations

from collections.abc import Awaitable, Callable

from coding_agent.wire.local import LocalWire
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    WireMessage,
)


LocalWireApprovalHandler = Callable[[ApprovalRequest], Awaitable[ApprovalResponse]]
LocalWireEmitHandler = Callable[[WireMessage], Awaitable[None]]


class LocalWireConsumer:
    def __init__(
        self,
        wire: LocalWire,
        approval_handler: LocalWireApprovalHandler,
        emit_handler: LocalWireEmitHandler | None = None,
    ) -> None:
        self._wire = wire
        self._approval_handler = approval_handler
        self._emit_handler = emit_handler

    async def emit(self, msg: WireMessage) -> None:
        if self._emit_handler is not None:
            await self._emit_handler(msg)
            return
        await self._wire.send(msg)

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        return await self._approval_handler(req)


__all__ = [
    "LocalWireApprovalHandler",
    "LocalWireConsumer",
    "LocalWireEmitHandler",
]
