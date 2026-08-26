"""Turn admission, run, cancel, and subagent publish."""

from __future__ import annotations

import logging
import asyncio
import uuid
from datetime import (
    UTC,
    datetime,
)
from typing import (
    Any,
    cast,
)
from agentkit.runtime import (
    DuplicateRuntimeMessageError,
    RuntimeMessage,
    RuntimeMessageKind,
)
from coding_agent.runs import (
    RemoteLoopOwnershipRetired,
    RuntimeResumeContext as SessionResumeContext,
)
from coding_agent.adapter import PipelineAdapter  # noqa: F401
from coding_agent.wire.protocol import WireMessage
from coding_agent.server.session.models import CancelTurnResult
from coding_agent.server.session.models import CancelTurnStatus
from coding_agent.server.session.models import Session
from coding_agent.server.session.models import _session_is_attached
from coding_agent.server.session.models import _subagent_message_id

logger = logging.getLogger("coding_agent.server.session_manager")


class TurnOps:
    async def _send_session_wire_message(
        self,
        session: Session,
        message: WireMessage,
    ) -> None:
        persist = getattr(self, "persist_chat_wire_message", None)
        if callable(persist):
            await persist(session, message)
        await self._append_runtime_wire_event(session, message)
        await session.wire.send(message)

    def _turn_lock_for(self, session_id: str) -> asyncio.Lock:
        lock = self._session_turn_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_turn_locks[session_id] = lock
        return lock

    async def prepare_session_turn(self, session_id: str) -> Session:
        return cast(
            Session,
            await self._runtime_turn_admission.prepare_session_turn(session_id),
        )

    async def cancel_session_turn(self, session_id: str) -> CancelTurnResult:
        """Request cancellation for the active turn without closing the session."""
        async with self._lock:
            await self._assert_owner(session_id)
            session = await self.get_session_async(session_id)
            result = await self._runtime_cancel_orchestration.cancel(
                session,
                task=session.task,
            )
            return CancelTurnResult(
                session_id=session_id,
                turn_id=result.turn_id,
                status=cast(CancelTurnStatus, result.status),
            )

    def _schedule_cancel_observation(
        self,
        session_id: str,
        task: asyncio.Task[Any],
    ) -> None:
        _ = asyncio.create_task(
            self._observe_cancelled_turn(session_id=session_id, task=task)
        )

    async def _observe_cancelled_turn(
        self,
        *,
        session_id: str,
        task: asyncio.Task[Any],
    ) -> None:
        await self._runtime_cancel_observation_finalizer.finalize(
            session_id=session_id,
            task=task,
        )

    async def run_agent(
        self,
        session_id: str,
        prompt: str,
        *,
        run_id_override: str | None = None,
        resume_context: SessionResumeContext | None = None,
    ) -> None:
        if _session_is_attached(await self.get_session_async(session_id)):
            raise RemoteLoopOwnershipRetired()

        async def run_admitted_turn(session: object) -> None:
            admitted_session = cast(Session, session)
            run_id = run_id_override or uuid.uuid4().hex
            await self._runtime_turn_service.run(
                admitted_session,
                prompt=prompt,
                run_id=run_id,
                resume_context=resume_context,
                current_task=asyncio.current_task(),
            )

        await self._runtime_turn_admission.run_exclusive(
            session_id,
            run_admitted_turn,
        )

    async def publish_subagent_message(
        self,
        session_id: str,
        text: str,
        *,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")
        if message_id is not None and not message_id:
            raise ValueError("message_id must be None or a non-empty string")

        async with self._lock:
            await self._assert_owner(session_id)
            session = await self.get_session_async(session_id)
            effective_message_id = message_id or _subagent_message_id(session_id)
            payload: dict[str, Any] = {"text": text}
            if metadata is not None:
                payload["metadata"] = dict(metadata)

            try:
                await session.runtime_message_bus.publish(
                    RuntimeMessage(
                        message_id=effective_message_id,
                        kind=RuntimeMessageKind.SUBAGENT_MESSAGE,
                        payload=payload,
                    )
                )
            except DuplicateRuntimeMessageError as exc:
                if exc.message_id != effective_message_id:
                    raise
                logger.info(
                    "subagent_message already published for session %s message %s",
                    session_id,
                    effective_message_id,
                )
            session.last_activity = datetime.now(UTC)
            await self._persist_session_async(session)
        return True
