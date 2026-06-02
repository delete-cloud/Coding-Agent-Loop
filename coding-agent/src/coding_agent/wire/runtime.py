from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from coding_agent.wire.protocol import (
    CompletionStatus,
    StreamDelta,
    TurnEnd,
    WireMessage,
)


class RuntimeTurnWireSession(Protocol):
    id: str


RuntimeTurnWireEmitter = Callable[
    [RuntimeTurnWireSession, WireMessage],
    Awaitable[None],
]
RuntimeTurnWireLogger = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class RuntimeTurnWire:
    session_id: str
    run_id: str
    emit_message: RuntimeTurnWireEmitter
    log_exception: RuntimeTurnWireLogger | None = None

    async def notify_generic_error(
        self,
        session: RuntimeTurnWireSession,
        exc: Exception,
    ) -> None:
        if session.id != self.session_id:
            raise ValueError(
                "runtime turn wire session mismatch: "
                f"expected {self.session_id}, got {session.id}"
            )
        if self.log_exception is not None:
            self.log_exception("HTTP session turn failed")
        await self.emit_message(
            session,
            StreamDelta(
                session_id=self.session_id,
                agent_id="",
                content=f"Error: {exc}",
            ),
        )
        await self.emit_message(
            session,
            TurnEnd(
                session_id=self.session_id,
                agent_id="",
                turn_id=self.run_id,
                completion_status=CompletionStatus.ERROR,
            ),
        )


__all__ = [
    "RuntimeTurnWire",
    "RuntimeTurnWireEmitter",
    "RuntimeTurnWireLogger",
    "RuntimeTurnWireSession",
]
