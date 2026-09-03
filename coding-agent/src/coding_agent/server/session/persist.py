"""Authoritative UoW persist and session metadata writes."""

from __future__ import annotations

import logging
import uuid
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
from coding_agent.events.connected_chat import (
    ChatCommandAdmission,
    RootRunAlreadySettledError,
)
from coding_agent.stores.durable_local import SQLiteLocalDurableStore
from coding_agent.stores.durable_pg import PGDurableStore
from coding_agent.stores.runtime_store import (
    AuthoritativeCommit,
    AuthoritativeUnitOfWork,
    EffectLedgerSlot,
    EventRecord,
    JSONObject,
    MailboxDispositionSlot,
)
from coding_agent.server.stores.session_owner_store import SessionOwnershipConflictError
from coding_agent.runtime_activation import assert_legacy_terminal_writer_allowed
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
        *,
        projection_epoch: str,
    ) -> str:
        if not isinstance(projection_epoch, str) or not projection_epoch:
            raise ValueError(
                f"session {session.id} has invalid projection_epoch: "
                f"{projection_epoch!r}"
            )
        event_id = (
            f"{session.id}:{event_kind}:{session.current_turn_id or 'none'}"
            f":e{projection_epoch}"
        )
        if event_id_suffix:
            return f"{event_id}:{event_id_suffix}"
        return event_id

    async def _current_projection_epoch(self, session_id: str) -> str:
        # Load once per persist call. Do not cache across persists: restore can
        # bump projection_epoch on the store without going through SessionManager.
        store = self._authoritative_store()
        if store is None:
            return "0"
        load_fact = getattr(store, "load_session_fact_source", None)
        if not callable(load_fact):
            return "0"
        fact = await load_fact(session_id)
        if fact is None:
            return "0"
        epoch = fact.projection_epoch
        if not isinstance(epoch, str) or not epoch:
            logger.error(
                "invalid projection_epoch for session %s: %r",
                session_id,
                epoch,
            )
            raise ValueError(
                f"session {session_id} has invalid projection_epoch: {epoch!r}"
            )
        return epoch

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

    async def admit_chat_command(
        self,
        session_id: str,
        *,
        prompt: str,
        command_id: str,
        parent_run_id: str | None = None,
    ) -> ChatCommandAdmission:
        session = await self.get_session_async(session_id)
        store = self._authoritative_store()
        if store is None:
            raise RuntimeError("durable authoritative store is not configured")
        authority = self._owner_authorities.get(session_id)
        if authority is None:
            raise SessionOwnershipConflictError(
                "chat admission requires owner authority"
            )
        if session.tape_id is None:
            await self.ensure_session_runtime(session_id)
            session = await self.get_session_async(session_id)
        admission = await store.admit_chat_command(
            authority,
            prompt=prompt,
            command_id=command_id,
            parent_run_id=parent_run_id,
            session_state=cast(JSONObject, session.to_store_data()),
        )
        if not admission.idempotent:
            session.current_turn_id = admission.run_id
            session.turn_in_progress = True
            self._session_cache[session_id] = session
            await self._publish_admitted_chat_command(session_id, admission)
        return admission

    async def _publish_admitted_chat_command(
        self, session_id: str, admission: ChatCommandAdmission
    ) -> None:
        from coding_agent.events.connected_chat import (
            CONNECTED_CHAT_PROJECTION,
            ConnectedChatCursor,
        )

        store = self._authoritative_store()
        if store is None or admission.session_seq is None:
            return
        after_seq = str(int(admission.session_seq) - 1)
        fact = await store.load_session_fact_source(session_id)
        if fact is None:
            return
        snapshot = await store.snapshot_chat_events(
            session_id,
            ConnectedChatCursor(
                v=1,
                kind="chat",
                session_id=session_id,
                projection=CONNECTED_CHAT_PROJECTION,
                epoch=fact.projection_epoch,
                after_seq=after_seq,
                high_water_seq=admission.session_seq,
            ),
            1,
        )
        if not snapshot.events:
            return
        event = snapshot.events[0]
        for subscriber in tuple(self._chat_subscribers.get(event.session_id, ())):
            subscriber.publish(event)

    def can_settle_root_run_authoritatively(self) -> bool:
        return self._authoritative_store() is not None

    async def settle_root_run(
        self,
        session_id: str,
        *,
        run_id: str,
        outcome: str,
        result: str | None = None,
        error: str | None = None,
        result_payload: JSONObject | None = None,
        extra_metadata: JSONObject | None = None,
    ) -> AuthoritativeCommit:
        store = self._authoritative_store()
        if store is None:
            raise RuntimeError("durable authoritative store is not configured")
        authority = self._owner_authorities.get(session_id)
        if authority is None:
            raise SessionOwnershipConflictError(
                "root settlement requires owner authority"
            )
        await self.flush_chat_assistant_buffer(session_id)
        settlement = await store.settle_root_run(
            authority,
            run_id=run_id,
            outcome=outcome,
            result=result,
            error=error,
            result_payload=result_payload,
            extra_metadata=extra_metadata,
        )
        session = await self.get_session_async(session_id)
        if session.current_turn_id == run_id:
            session.turn_in_progress = False
            session.turn_status = outcome
            self._session_cache[session_id] = session
        await self._publish_chat_commit(settlement)
        return settlement

    async def persist_chat_wire_message(
        self, session: Session, message: object
    ) -> None:
        from coding_agent.wire.protocol import (
            StreamDelta,
            ThinkingDelta,
            ToolCallDelta,
            ToolResultDelta,
        )

        if self._authoritative_store() is None:
            return
        run_id = session.current_turn_id
        if run_id is None:
            return
        if isinstance(message, StreamDelta):
            previous = self._chat_assistant_buffers.get(session.id)
            if previous is not None and previous[0] != run_id:
                await self.flush_chat_assistant_buffer(session.id)
            current = self._chat_assistant_buffers.get(session.id)
            prefix = current[1] if current is not None and current[0] == run_id else ""
            self._chat_assistant_buffers[session.id] = (
                run_id,
                prefix + message.content,
            )
            return
        await self.flush_chat_assistant_buffer(session.id)
        if isinstance(message, ThinkingDelta):
            await self._commit_and_publish_chat_event(
                session,
                "thinking",
                {"run_id": run_id, "text": message.text},
                run_id,
            )
            return
        if isinstance(message, ToolCallDelta):
            await self._commit_and_publish_chat_event(
                session,
                "tool_call",
                {
                    "run_id": run_id,
                    "call_id": message.call_id,
                    "tool_name": message.tool_name,
                    "arguments": message.arguments,
                },
                run_id,
            )
            return
        if isinstance(message, ToolResultDelta):
            output = message.display_result
            if not output and isinstance(message.result, str):
                output = message.result
            await self._commit_and_publish_chat_event(
                session,
                "tool_result",
                {
                    "run_id": run_id,
                    "call_id": message.call_id,
                    "output": output,
                    "is_error": message.is_error,
                },
                run_id,
            )

    async def flush_chat_assistant_buffer(self, session_id: str) -> None:
        buffered = self._chat_assistant_buffers.pop(session_id, None)
        if buffered is None:
            return
        run_id, text = buffered
        if not text:
            return
        session = await self.get_session_async(session_id)
        await self._commit_and_publish_chat_event(
            session,
            "assistant_message",
            {"run_id": run_id, "text": text},
            run_id,
        )

    async def _commit_and_publish_chat_event(
        self,
        session: Session,
        event_kind: str,
        payload: JSONObject,
        run_id: str,
    ) -> None:
        store = self._authoritative_store()
        if store is None:
            return
        authority = self._owner_authorities.get(session.id)
        if authority is None:
            raise SessionOwnershipConflictError(
                "chat event persist requires owner authority"
            )
        projection_epoch = await self._current_projection_epoch(session.id)
        try:
            commit = await store.commit_authoritative_uow(
                authority,
                AuthoritativeUnitOfWork(
                    event=EventRecord(
                        event_id=self._boundary_event_id(
                            session,
                            event_kind,
                            uuid.uuid4().hex,
                            projection_epoch=projection_epoch,
                        ),
                        session_id=session.id,
                        event_kind=event_kind,
                        payload=payload,
                        created_at=datetime.now(UTC),
                    ),
                    session_state=cast(JSONObject, session.to_store_data()),
                    require_unsettled_root_run_id=run_id,
                ),
            )
        except RootRunAlreadySettledError:
            return
        await self._publish_chat_commit(commit)

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
        projection_epoch = await self._current_projection_epoch(session.id)
        await store.commit_authoritative_uow(
            authority,
            AuthoritativeUnitOfWork(
                event=EventRecord(
                    event_id=self._boundary_event_id(
                        session,
                        event_kind,
                        event_id_suffix,
                        projection_epoch=projection_epoch,
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
        assert_legacy_terminal_writer_allowed(
            cast(dict[str, object], session.to_store_data())
        )
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
