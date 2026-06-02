"""Structural contracts for durable runtime stores."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from coding_agent.runtime_store import (
    AgentInteractionRecord,
    AgentRunRecord,
    JSONObject,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
)


@runtime_checkable
class RuntimeRunLifecycleStore(Protocol):
    async def create_agent_run(self, record: AgentRunRecord) -> AgentRunRecord: ...

    async def update_agent_run(
        self,
        run_id: str,
        *,
        status: str,
        ended_at: datetime | None,
        metadata: JSONObject,
        result: JSONObject,
        error: str | None,
    ) -> AgentRunRecord: ...


@runtime_checkable
class RuntimeRunStore(RuntimeRunLifecycleStore, Protocol):
    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None: ...

    async def list_agent_runs(self, session_id: str) -> list[AgentRunRecord]: ...

    async def claim_attached_executor_run(
        self,
        *,
        session_id: str | None,
        executor_kind: str,
        claim_metadata: JSONObject,
    ) -> AgentRunRecord | None: ...

    async def claim_external_worker_run(
        self,
        *,
        session_id: str | None,
        executor_kind: str,
        claim_metadata: JSONObject,
    ) -> AgentRunRecord | None: ...


@runtime_checkable
class RuntimeEventStore(Protocol):
    async def append_runtime_event(
        self,
        record: RuntimeEventRecord,
    ) -> RuntimeEventRecord: ...

    async def load_runtime_event(
        self,
        event_id: str,
    ) -> RuntimeEventRecord | None: ...

    async def replay_runtime_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> list[RuntimeEventRecord]: ...


@runtime_checkable
class RuntimeCheckpointStore(Protocol):
    async def save_message_snapshot(
        self,
        record: RunMessageSnapshotRecord,
    ) -> RunMessageSnapshotRecord: ...

    async def load_message_snapshot(
        self,
        snapshot_id: str,
    ) -> RunMessageSnapshotRecord | None: ...

    async def list_message_snapshots(
        self,
        run_id: str,
    ) -> list[RunMessageSnapshotRecord]: ...


@runtime_checkable
class RuntimeInteractionStore(Protocol):
    async def create_agent_interaction(
        self,
        record: AgentInteractionRecord,
    ) -> AgentInteractionRecord: ...

    async def load_agent_interaction(
        self,
        interaction_id: str,
    ) -> AgentInteractionRecord | None: ...

    async def list_agent_interactions(
        self,
        run_id: str,
    ) -> list[AgentInteractionRecord]: ...

    async def resolve_agent_interaction(
        self,
        interaction_id: str,
        *,
        status: str,
        response_payload: JSONObject,
        resolved_at: datetime,
    ) -> AgentInteractionRecord: ...


@runtime_checkable
class RuntimeStore(
    RuntimeRunStore,
    RuntimeEventStore,
    RuntimeCheckpointStore,
    RuntimeInteractionStore,
    Protocol,
):
    """Full durable runtime store surface used by the current control plane."""
