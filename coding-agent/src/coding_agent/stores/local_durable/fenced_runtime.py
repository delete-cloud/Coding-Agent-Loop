"""Fenced SQLite runtime store wrapper."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from coding_agent.stores.runtime_store import (
    AgentRunRecord,
    AgentInteractionRecord,
    RuntimeEventRecord,
    RunMessageSnapshotRecord,
    SQLiteRuntimeStore,
)
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
)
from coding_agent.stores.local_durable.store import SQLiteLocalDurableStore


class FencedSQLiteRuntimeStore:
    def __init__(
        self,
        *,
        durable_store: SQLiteLocalDurableStore,
        path: Path,
        authority_for_session: Callable[[str], OwnerAuthority],
        authorities: Callable[[], Mapping[str, OwnerAuthority]],
    ) -> None:
        self._durable_store = durable_store
        self._delegate = SQLiteRuntimeStore(path)
        self._authority_for_session = authority_for_session
        self._authorities = authorities

    async def create_agent_run(self, record: AgentRunRecord) -> AgentRunRecord:
        return await self._durable_store.create_agent_run(
            self._authority_for_session(record.session_id),
            record,
        )

    async def update_agent_run(
        self,
        run_id: str,
        *,
        status: str,
        ended_at: datetime | None,
        metadata: dict[str, Any],
        result: dict[str, Any],
        error: str | None,
    ) -> AgentRunRecord:
        session_id = self._require_session_id_for_run(run_id)
        return await self._durable_store.update_agent_run(
            self._authority_for_session(session_id),
            run_id,
            status=status,
            ended_at=ended_at,
            metadata=metadata,
            result=result,
            error=error,
        )

    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None:
        return await self._delegate.load_agent_run(run_id)

    async def list_agent_runs(self, session_id: str) -> list[AgentRunRecord]:
        return await self._delegate.list_agent_runs(session_id)

    async def claim_attached_executor_run(
        self,
        *,
        session_id: str | None,
        executor_kind: str,
        claim_metadata: dict[str, Any],
    ) -> AgentRunRecord | None:
        return await self._durable_store.claim_attached_executor_run(
            self._authorities(),
            session_id=session_id,
            executor_kind=executor_kind,
            claim_metadata=claim_metadata,
        )

    async def claim_external_worker_run(
        self,
        *,
        session_id: str | None,
        executor_kind: str,
        claim_metadata: dict[str, Any],
    ) -> AgentRunRecord | None:
        return await self._durable_store.claim_attached_executor_run(
            self._authorities(),
            session_id=session_id,
            executor_kind=executor_kind,
            claim_metadata=claim_metadata,
        )

    async def append_runtime_event(
        self,
        record: RuntimeEventRecord,
    ) -> RuntimeEventRecord:
        session_id = self._require_session_id_for_run(record.run_id)
        return await self._durable_store.append_runtime_event(
            self._authority_for_session(session_id),
            record,
        )

    async def load_runtime_event(
        self,
        event_id: str,
    ) -> RuntimeEventRecord | None:
        return await self._delegate.load_runtime_event(event_id)

    async def replay_runtime_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> list[RuntimeEventRecord]:
        return await self._delegate.replay_runtime_events(
            run_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    async def save_message_snapshot(
        self,
        record: RunMessageSnapshotRecord,
    ) -> RunMessageSnapshotRecord:
        session_id = self._require_session_id_for_run(record.run_id)
        return await self._durable_store.save_message_snapshot(
            self._authority_for_session(session_id),
            record,
        )

    async def load_message_snapshot(
        self,
        snapshot_id: str,
    ) -> RunMessageSnapshotRecord | None:
        return await self._delegate.load_message_snapshot(snapshot_id)

    async def list_message_snapshots(
        self,
        run_id: str,
    ) -> list[RunMessageSnapshotRecord]:
        return await self._delegate.list_message_snapshots(run_id)

    async def create_agent_interaction(
        self,
        record: AgentInteractionRecord,
    ) -> AgentInteractionRecord:
        session_id = self._require_session_id_for_run(record.run_id)
        return await self._durable_store.create_agent_interaction(
            self._authority_for_session(session_id),
            record,
        )

    async def load_agent_interaction(
        self,
        interaction_id: str,
    ) -> AgentInteractionRecord | None:
        return await self._delegate.load_agent_interaction(interaction_id)

    async def list_agent_interactions(
        self,
        run_id: str,
    ) -> list[AgentInteractionRecord]:
        return await self._delegate.list_agent_interactions(run_id)

    async def resolve_agent_interaction(
        self,
        interaction_id: str,
        *,
        status: str,
        response_payload: dict[str, Any],
        resolved_at: datetime,
    ) -> AgentInteractionRecord:
        interaction = await self._delegate.load_agent_interaction(interaction_id)
        if interaction is None:
            raise KeyError(f"agent interaction not found: {interaction_id}")
        session_id = self._require_session_id_for_run(interaction.run_id)
        return await self._durable_store.resolve_agent_interaction(
            self._authority_for_session(session_id),
            interaction_id,
            status=status,
            response_payload=response_payload,
            resolved_at=resolved_at,
        )

    def _require_session_id_for_run(self, run_id: str) -> str:
        session_id = self._durable_store.session_id_for_run(run_id)
        if session_id is None:
            raise KeyError(f"agent run not found: {run_id}")
        return session_id
