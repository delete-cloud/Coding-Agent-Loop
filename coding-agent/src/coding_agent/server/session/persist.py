"""Authoritative UoW persist and session metadata writes."""

from __future__ import annotations

import logging
import asyncio
from collections.abc import Callable
from datetime import (
    UTC,
    datetime,
)
from typing import (
    Any,
    cast,
)
from coding_agent.stores.durable_local import SQLiteLocalDurableStore
from coding_agent.stores.durable_pg import PGDurableStore
from coding_agent.stores.runtime_store import (
    AuthoritativeUnitOfWork,
    EffectLedgerSlot,
    EventRecord,
    JSONObject,
    MailboxDispositionSlot,
)
from coding_agent.server.stores.session_owner_store import SessionOwnershipConflictError
from coding_agent.server.session.models import Session
from coding_agent.server.session.models import T

logger = logging.getLogger("coding_agent.server.session_manager")


class PersistOps:
    async def _run_store_io(self, func: Callable[..., T], /, *args: object) -> T:
        def run_guarded() -> T:
            with self._store_io_guard:
                return func(*args)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, run_guarded)

    def _authoritative_store(self) -> SQLiteLocalDurableStore | PGDurableStore | None:
        if self._local_durable_store is not None:
            return self._local_durable_store
        return self._pg_durable_store

    def _boundary_event_id(
        self,
        session: Session,
        event_kind: str,
        event_id_suffix: str | None = None,
    ) -> str:
        event_id = f"{session.id}:{event_kind}:{session.current_turn_id or 'none'}"
        if event_id_suffix:
            return f"{event_id}:{event_id_suffix}"
        return event_id

    def _session_boundary_payload(self, session: Session) -> JSONObject:
        return {
            "turn_id": session.current_turn_id,
            "turn_in_progress": session.turn_in_progress,
        }

    def _approval_boundary_id(self, session: Session) -> str | None:
        pending = session.approval_coordinator.pending_request
        if pending is not None:
            return pending.request_id
        projection = session.approval_response
        if isinstance(projection, dict):
            request_id = projection.get("request_id")
            if isinstance(request_id, str) and request_id:
                return request_id
        return None

    def _approval_effect_id(self, session_id: str, request_id: str) -> str:
        return f"{session_id}:approval:{request_id}"

    async def _commit_session_uow(
        self,
        session: Session,
        *,
        event_kind: str,
        payload: JSONObject,
        created_at: datetime,
        include_mailbox: bool,
        event_id_suffix: str | None = None,
        effect: EffectLedgerSlot | None = None,
    ) -> None:
        self._session_cache[session.id] = session
        store = self._authoritative_store()
        if store is None:
            raise RuntimeError("durable authoritative store is not configured")
        authority = self._owner_authorities.get(session.id)
        if authority is None:
            raise SessionOwnershipConflictError(
                "session metadata mutation requires owner authority"
            )
        mailbox = None
        if include_mailbox and session.current_turn_id is not None:
            mailbox = MailboxDispositionSlot(
                slot_id=f"turn:{session.current_turn_id}",
                lane="turn",
                disposition="in_flight" if session.turn_in_progress else "settled",
                payload={"run_id": session.current_turn_id},
            )
        session_state = cast(JSONObject, session.to_store_data())
        await store.commit_authoritative_uow(
            authority,
            AuthoritativeUnitOfWork(
                event=EventRecord(
                    event_id=self._boundary_event_id(
                        session,
                        event_kind,
                        event_id_suffix,
                    ),
                    session_id=session.id,
                    event_kind=event_kind,
                    payload=payload,
                    created_at=created_at,
                ),
                session_state=session_state,
                mailbox=mailbox,
                effect=effect,
            ),
        )

    async def _persist_session_async(self, session: Session) -> None:
        self._session_cache[session.id] = session
        payload = cast(dict[str, Any], session.to_store_data())
        store = self._authoritative_store()
        if store is not None:
            authority = self._owner_authorities.get(session.id)
            if authority is None:
                raise SessionOwnershipConflictError(
                    "session metadata mutation requires owner authority"
                )
            await store.save_session(authority, payload)
            return
        await self._run_store_io(
            self._store.save,
            session.id,
            payload,
        )

    async def _persist_turn_started(self, session: Session) -> None:
        if self._authoritative_store() is None:
            await self._persist_session_async(session)
            return
        await self._commit_session_uow(
            session,
            event_kind="harness.TurnStarted",
            payload=self._session_boundary_payload(session),
            created_at=datetime.now(UTC),
            include_mailbox=True,
        )

    async def _persist_turn_settled(self, session: Session) -> None:
        if self._authoritative_store() is None:
            await self._persist_session_async(session)
            return
        await self._commit_session_uow(
            session,
            event_kind="harness.TurnSettled",
            payload=self._session_boundary_payload(session),
            created_at=datetime.now(UTC),
            include_mailbox=True,
        )

    async def _persist_approval_requested(self, session: Session) -> None:
        request_id = self._approval_boundary_id(session)
        if request_id is None or self._authoritative_store() is None:
            await self._persist_session_async(session)
            return
        await self._commit_session_uow(
            session,
            event_kind="harness.ApprovalRequested",
            payload=self._session_boundary_payload(session),
            created_at=datetime.now(UTC),
            include_mailbox=session.current_turn_id is not None,
            event_id_suffix=request_id,
            effect=EffectLedgerSlot(
                effect_id=self._approval_effect_id(session.id, request_id),
                status="prepared",
                payload={"request_id": request_id},
            ),
        )

    async def _persist_approval_decided(self, session: Session) -> None:
        request_id = self._approval_boundary_id(session)
        if request_id is None or self._authoritative_store() is None:
            await self._persist_session_async(session)
            return
        await self._commit_session_uow(
            session,
            event_kind="harness.ApprovalDecided",
            payload=self._session_boundary_payload(session),
            created_at=datetime.now(UTC),
            include_mailbox=session.current_turn_id is not None,
            event_id_suffix=request_id,
            effect=EffectLedgerSlot(
                effect_id=self._approval_effect_id(session.id, request_id),
                status="settled",
                payload={"request_id": request_id},
            ),
        )

    def _persist_session(self, session: Session) -> None:
        if self._local_durable_store is not None or self._pg_durable_store is not None:
            raise RuntimeError(
                "synchronous session persistence is unavailable for fenced durable storage"
            )
        self._session_cache[session.id] = session
        self._store.save(session.id, cast(dict[str, Any], session.to_store_data()))
