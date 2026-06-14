from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from agentkit.checkpoint.models import CheckpointMeta

from coding_agent.events import DisplayEvent, RuntimeEventReplayService
from coding_agent.stores.runtime_store import (
    AgentInteractionRecord,
    AgentRunRecord,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
)


class RuntimeQueryStore(Protocol):
    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None: ...

    async def list_agent_runs(self, session_id: str) -> list[AgentRunRecord]: ...

    async def list_agent_interactions(
        self,
        run_id: str,
    ) -> list[AgentInteractionRecord]: ...

    async def load_agent_interaction(
        self,
        interaction_id: str,
    ) -> AgentInteractionRecord | None: ...

    async def load_message_snapshot(
        self,
        snapshot_id: str,
    ) -> RunMessageSnapshotRecord | None: ...

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


class RuntimeQuerySession(Protocol):
    id: str
    tape_id: str | None


CheckpointLister = Callable[[str], Awaitable[list[CheckpointMeta]]]


class RuntimeCheckpointQueryBackend(Protocol):
    async def list(self, tape_id: str) -> list[CheckpointMeta]: ...


RuntimeCheckpointQueryBackendProvider = Callable[[], RuntimeCheckpointQueryBackend]


@dataclass(frozen=True)
class RuntimeCheckpointQueryService:
    checkpoint_service: RuntimeCheckpointQueryBackendProvider

    async def list_checkpoints(
        self,
        session: RuntimeQuerySession,
    ) -> list[CheckpointMeta]:
        if session.tape_id is None:
            return []
        return await self.checkpoint_service().list(session.tape_id)


@dataclass(frozen=True)
class RuntimeQueryService:
    store: RuntimeQueryStore | None
    active_resume_blocking_statuses: frozenset[str] = frozenset(
        {"requested", "claimed", "running"}
    )

    async def load_runtime_run(self, run_id: str) -> AgentRunRecord:
        record = await self._require_store().load_agent_run(run_id)
        if record is None:
            raise KeyError(f"runtime run not found: {run_id}")
        return record

    async def list_runtime_runs(self, session_id: str) -> list[AgentRunRecord]:
        return await self._require_store().list_agent_runs(session_id)

    async def latest_runtime_run(self, session_id: str) -> AgentRunRecord | None:
        runs = await self.list_runtime_runs(session_id)
        if not runs:
            return None
        return max(runs, key=lambda run: (run.started_at, run.run_id))

    async def latest_runtime_event_id(self, run: AgentRunRecord) -> str | None:
        events = await self.replay_runtime_events(run.run_id, limit=1000)
        if not events:
            return None
        sequenced_events = [event for event in events if event.sequence is not None]
        if sequenced_events:
            return max(sequenced_events, key=lambda event: event.sequence or 0).event_id
        return max(events, key=lambda event: event.created_at).event_id

    async def list_runtime_interactions(
        self,
        run_id: str,
    ) -> list[AgentInteractionRecord]:
        return await self._require_store().list_agent_interactions(run_id)

    async def load_runtime_interaction(
        self,
        interaction_id: str,
    ) -> AgentInteractionRecord:
        record = await self._require_store().load_agent_interaction(interaction_id)
        if record is None:
            raise KeyError(f"runtime interaction not found: {interaction_id}")
        return record

    async def load_runtime_message_snapshot(
        self,
        run_id: str,
    ) -> RunMessageSnapshotRecord:
        snapshot_id = f"{run_id}:latest"
        record = await self._require_store().load_message_snapshot(snapshot_id)
        if record is None:
            raise KeyError(f"runtime message snapshot not found: {snapshot_id}")
        return record

    async def replay_runtime_events(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        limit: int = 1000,
    ) -> list[RuntimeEventRecord]:
        return await RuntimeEventReplayService(
            self._require_store()
        ).replay_runtime_events(
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
        return await RuntimeEventReplayService(
            self._require_store()
        ).replay_display_events(
            run_id,
            last_event_id=last_event_id,
            limit=limit,
        )

    async def session_resume_metadata(
        self,
        session: RuntimeQuerySession,
        *,
        list_checkpoints: CheckpointLister,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "resumable": False,
            "last_run_id": None,
            "last_run_status": None,
            "last_interrupted_run_id": None,
            "resume_from_event_id": None,
            "checkpoint_count": 0,
            "latest_checkpoint_id": None,
            "latest_checkpoint_label": None,
        }
        try:
            latest_run = await self.latest_runtime_run(session.id)
        except RuntimeError:
            latest_run = None
        if latest_run is not None:
            metadata["last_run_id"] = latest_run.run_id
            metadata["last_run_status"] = latest_run.status
            metadata["resumable"] = (
                latest_run.status not in self.active_resume_blocking_statuses
            )
            metadata["resume_from_event_id"] = await self.latest_runtime_event_id(
                latest_run
            )
            interrupted_runs = [
                run
                for run in await self.list_runtime_runs(session.id)
                if run.status == "interrupted"
            ]
            if interrupted_runs:
                metadata["last_interrupted_run_id"] = max(
                    interrupted_runs,
                    key=lambda run: (run.started_at, run.run_id),
                ).run_id
        if session.tape_id is not None:
            checkpoints = await list_checkpoints(session.id)
            metadata["checkpoint_count"] = len(checkpoints)
            if checkpoints:
                latest_checkpoint = max(
                    checkpoints,
                    key=lambda checkpoint: (
                        checkpoint.created_at,
                        checkpoint.checkpoint_id,
                    ),
                )
                metadata["latest_checkpoint_id"] = latest_checkpoint.checkpoint_id
                metadata["latest_checkpoint_label"] = latest_checkpoint.label
        return metadata

    def _require_store(self) -> RuntimeQueryStore:
        if self.store is None:
            raise RuntimeError("runtime store is not configured")
        return self.store


__all__ = [
    "CheckpointLister",
    "RuntimeCheckpointQueryBackend",
    "RuntimeCheckpointQueryBackendProvider",
    "RuntimeCheckpointQueryService",
    "RuntimeQueryService",
    "RuntimeQuerySession",
    "RuntimeQueryStore",
]
