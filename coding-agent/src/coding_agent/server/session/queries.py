"""Runtime/tape query and recovery facades."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from agentkit.storage.protocols import (
    TapeDebugStore,
    TapeInfo,
    TapeSearchResult,
)
from coding_agent.stores.runtime_store import (
    AgentInteractionRecord,
    AgentRunRecord,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
)
from coding_agent.events import DisplayEvent
from coding_agent.runs import RuntimeWireEventRecorder
from coding_agent.wire.protocol import WireMessage
from coding_agent.server.session.models import Session

logger = logging.getLogger("coding_agent.server.session_manager")


class QueryOps:
    async def load_runtime_run(self, run_id: str) -> AgentRunRecord:
        return await self._runtime_control_services.queries().load_runtime_run(run_id)

    async def list_runtime_runs(self, session_id: str) -> list[AgentRunRecord]:
        return await self._runtime_control_services.queries().list_runtime_runs(
            session_id
        )

    async def list_active_runtime_runs(
        self,
        session_id: str,
    ) -> list[AgentRunRecord]:
        return await self._runtime_control_services.queries().list_active_runtime_runs(
            session_id
        )

    async def session_resume_metadata(self, session_id: str) -> dict[str, Any]:
        session = await self.get_session_async(session_id)
        return await self._runtime_control_services.queries().session_resume_metadata(
            session,
            list_checkpoints=self.list_checkpoints,
        )

    async def list_runtime_interactions(
        self,
        run_id: str,
    ) -> list[AgentInteractionRecord]:
        return await self._runtime_control_services.queries().list_runtime_interactions(
            run_id
        )

    async def load_runtime_interaction(
        self,
        interaction_id: str,
    ) -> AgentInteractionRecord:
        return await self._runtime_control_services.queries().load_runtime_interaction(
            interaction_id
        )

    async def load_tape_debug_info(self, tape_id: str) -> TapeInfo | None:
        if not isinstance(self._tape_store, TapeDebugStore):
            return None
        return await self._tape_store.info(tape_id)

    async def search_tape_debug_entries(
        self,
        *,
        tape_id: str | None = None,
        kind: str | None = None,
        run_id: str | None = None,
        tool_call_id: str | None = None,
        anchor_type: str | None = None,
        limit: int = 100,
    ) -> list[TapeSearchResult]:
        if not isinstance(self._tape_store, TapeDebugStore):
            return []
        return await self._tape_store.search(
            tape_id=tape_id,
            kind=kind,
            run_id=run_id,
            tool_call_id=tool_call_id,
            anchor_type=anchor_type,
            limit=limit,
        )

    async def load_runtime_message_snapshot(
        self,
        run_id: str,
    ) -> RunMessageSnapshotRecord:
        return await self._runtime_control_services.queries().load_runtime_message_snapshot(
            run_id
        )

    async def replay_runtime_events(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        limit: int = 1000,
    ) -> list[RuntimeEventRecord]:
        return await self._runtime_control_services.queries().replay_runtime_events(
            run_id,
            last_event_id=last_event_id,
            limit=limit,
        )

    async def replay_display_events(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        limit: int = 1000,
    ) -> list[DisplayEvent]:
        return await self._runtime_control_services.queries().replay_display_events(
            run_id,
            last_event_id=last_event_id,
            limit=limit,
        )

    async def recover_stale_runtime_runs(
        self,
        *,
        recovered_at: datetime | None = None,
    ) -> int:
        return await self._runtime_control_services.run_recovery().recover_stale_runtime_runs(
            recovered_at=recovered_at,
        )

    async def _append_runtime_wire_event(
        self,
        session: Session,
        message: WireMessage,
    ) -> None:
        await RuntimeWireEventRecorder(self._runtime_store).append_wire_event(
            session,
            message,
        )
