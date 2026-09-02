"""Owner-fenced projector: committed facts to idempotent sinks."""

from __future__ import annotations

from typing import Protocol

from coding_agent.server.stores.session_owner_store import OwnerAuthority
from coding_agent.stores.runtime_store import EventRecord

PROJECTOR_SINKS = ("interaction", "wire_outbox")


class ProjectorStore(Protocol):
    async def load_projector_cursor(self, session_id: str) -> int: ...

    async def list_session_events_after(
        self,
        session_id: str,
        after_seq: int,
    ) -> tuple[EventRecord, ...]: ...

    async def upsert_projector_sink(
        self,
        authority: OwnerAuthority,
        *,
        event_id: str,
        sink: str,
        payload: dict[str, object],
    ) -> None: ...

    async def list_projector_sinks(
        self,
        session_id: str,
        event_id: str,
    ) -> frozenset[str]: ...

    async def list_wire_outbox_event_ids(self, session_id: str) -> tuple[str, ...]: ...

    async def advance_projector_cursor(
        self,
        authority: OwnerAuthority,
        session_seq: int,
    ) -> None: ...


async def project_takeover(
    store: ProjectorStore,
    authority: OwnerAuthority,
) -> int:
    """Replay facts after the durable cursor. Duplicate delivery is a no-op."""

    cursor = await store.load_projector_cursor(authority.session_id)
    events = await store.list_session_events_after(authority.session_id, cursor)
    projected = 0
    for event in events:
        seq = int(event.session_seq) if event.session_seq is not None else 0
        payload: dict[str, object] = {
            "event_id": event.event_id,
            "event_kind": event.event_kind,
            "session_seq": seq,
        }
        for sink in PROJECTOR_SINKS:
            await store.upsert_projector_sink(
                authority,
                event_id=event.event_id,
                sink=sink,
                payload=payload,
            )
        receipts = await store.list_projector_sinks(
            authority.session_id,
            event.event_id,
        )
        if receipts != frozenset(PROJECTOR_SINKS):
            break
        await store.advance_projector_cursor(authority, seq)
        projected += 1
    return projected
