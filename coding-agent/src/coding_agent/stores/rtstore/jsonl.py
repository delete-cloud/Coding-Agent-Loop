"""JSONL runtime store backend."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Final, cast
from coding_agent.stores.rtstore.records import (
    AgentInteractionRecord,
    AgentRunRecord,
    JSONObject,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
)
from coding_agent.stores.rtstore.harness import AuthoritativeWriteRefusedError
from coding_agent.stores.rtstore.payload import (
    _agent_run_from_payload,
    _agent_run_to_payload,
    _interaction_from_payload,
    _interaction_to_payload,
    _message_snapshot_from_payload,
    _message_snapshot_to_payload,
    _runtime_event_from_payload,
    _runtime_event_to_payload,
)
from coding_agent.stores.rtstore.validate import (
    _normalize_optional_error,
    _require_datetime,
    _require_json_object,
    _require_non_empty,
    _require_non_negative_int,
    _require_positive_int,
)


class JSONLRuntimeStore:
    _DEFAULT_REPLAY_LIMIT: Final[int] = 1000

    def __init__(self, root: Path) -> None:
        self._root = root
        self._lock = threading.Lock()
        self._root.mkdir(parents=True, exist_ok=True)

    async def create_agent_run(self, record: AgentRunRecord) -> AgentRunRecord:
        await self._append_jsonl("runs.jsonl", _agent_run_to_payload(record))
        return record

    async def update_agent_run(
        self,
        run_id: str,
        *,
        status: str,
        ended_at: datetime | None,
        metadata: JSONObject,
        result: JSONObject,
        error: str | None,
    ) -> AgentRunRecord:
        _require_non_empty("run_id", run_id)
        _require_non_empty("status", status)
        if ended_at is not None:
            _require_datetime("ended_at", ended_at)
        _require_json_object("metadata", metadata)
        _require_json_object("result", result)
        error = _normalize_optional_error(error)
        existing = await self.load_agent_run(run_id)
        if existing is None:
            raise KeyError(f"agent run not found: {run_id}")
        updated = AgentRunRecord(
            run_id=existing.run_id,
            session_id=existing.session_id,
            tape_id=existing.tape_id,
            parent_run_id=existing.parent_run_id,
            agent_id=existing.agent_id,
            status=status,
            started_at=existing.started_at,
            ended_at=ended_at,
            metadata=metadata,
            result=result,
            error=error,
            superseded_by_checkpoint_id=existing.superseded_by_checkpoint_id,
            superseded_at=existing.superseded_at,
        )
        await self._append_jsonl("runs.jsonl", _agent_run_to_payload(updated))
        return updated

    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None:
        _require_non_empty("run_id", run_id)
        runs = await self._latest_runs()
        return runs.get(run_id)

    async def list_agent_runs(self, session_id: str) -> list[AgentRunRecord]:
        _require_non_empty("session_id", session_id)
        runs = [
            run
            for run in (await self._latest_runs()).values()
            if run.session_id == session_id
        ]
        return sorted(runs, key=lambda run: (run.started_at, run.run_id))

    async def claim_attached_executor_run(
        self,
        *,
        session_id: str | None,
        executor_kind: str,
        claim_metadata: JSONObject,
    ) -> AgentRunRecord | None:
        if session_id is not None:
            _require_non_empty("session_id", session_id)
        _require_non_empty("executor_kind", executor_kind)
        _require_json_object("claim_metadata", claim_metadata)
        with self._lock:
            runs = self._latest_runs_sync()
            candidates = [
                run
                for run in runs.values()
                if run.status in {"requested", "expired"}
                and run.metadata.get("executor_ref_kind")
                in {"external_worker", "local_attached"}
                and run.metadata.get("executor_kind") == executor_kind
                and (session_id is None or run.session_id == session_id)
            ]
            if not candidates:
                return None
            selected = min(candidates, key=lambda run: (run.started_at, run.run_id))
            metadata = {**selected.metadata, **claim_metadata}
            claimed = AgentRunRecord(
                run_id=selected.run_id,
                session_id=selected.session_id,
                tape_id=selected.tape_id,
                parent_run_id=selected.parent_run_id,
                agent_id=selected.agent_id,
                status="claimed",
                started_at=selected.started_at,
                ended_at=selected.ended_at,
                metadata=cast(JSONObject, metadata),
                result=selected.result,
                error=selected.error,
                superseded_by_checkpoint_id=selected.superseded_by_checkpoint_id,
                superseded_at=selected.superseded_at,
            )
            self._append_jsonl_sync("runs.jsonl", _agent_run_to_payload(claimed))
            return claimed

    async def claim_external_worker_run(
        self,
        *,
        session_id: str | None,
        executor_kind: str,
        claim_metadata: JSONObject,
    ) -> AgentRunRecord | None:
        return await self.claim_attached_executor_run(
            session_id=session_id,
            executor_kind=executor_kind,
            claim_metadata=claim_metadata,
        )

    async def append_runtime_event(
        self,
        record: RuntimeEventRecord,
    ) -> RuntimeEventRecord:
        with self._lock:
            events = self._runtime_events_sync()
            for event in events:
                if event.event_id == record.event_id:
                    return event
            sequence = 1 + max(
                (event.sequence or 0 for event in events),
                default=0,
            )
            persisted = RuntimeEventRecord(
                event_id=record.event_id,
                run_id=record.run_id,
                event_kind=record.event_kind,
                payload=record.payload,
                created_at=record.created_at,
                sequence=sequence,
            )
            self._append_jsonl_sync(
                "events.jsonl", _runtime_event_to_payload(persisted)
            )
            return persisted

    async def replay_runtime_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = _DEFAULT_REPLAY_LIMIT,
    ) -> list[RuntimeEventRecord]:
        _require_non_empty("run_id", run_id)
        _require_non_negative_int("after_sequence", after_sequence)
        _require_positive_int("limit", limit)
        events = [
            event
            for event in await self._runtime_events()
            if event.run_id == run_id and (event.sequence or 0) > after_sequence
        ]
        return sorted(events, key=lambda event: event.sequence or 0)[:limit]

    async def load_runtime_event(
        self,
        event_id: str,
    ) -> RuntimeEventRecord | None:
        _require_non_empty("event_id", event_id)
        for event in await self._runtime_events():
            if event.event_id == event_id:
                return event
        return None

    async def save_message_snapshot(
        self,
        record: RunMessageSnapshotRecord,
    ) -> RunMessageSnapshotRecord:
        await self._append_jsonl(
            "snapshots.jsonl", _message_snapshot_to_payload(record)
        )
        return record

    async def load_message_snapshot(
        self,
        snapshot_id: str,
    ) -> RunMessageSnapshotRecord | None:
        _require_non_empty("snapshot_id", snapshot_id)
        snapshots = await self._latest_message_snapshots()
        return snapshots.get(snapshot_id)

    async def list_message_snapshots(
        self,
        run_id: str,
    ) -> list[RunMessageSnapshotRecord]:
        _require_non_empty("run_id", run_id)
        snapshots = [
            snapshot
            for snapshot in (await self._latest_message_snapshots()).values()
            if snapshot.run_id == run_id
        ]
        return sorted(snapshots, key=lambda item: (item.created_at, item.snapshot_id))

    async def create_agent_interaction(
        self,
        record: AgentInteractionRecord,
    ) -> AgentInteractionRecord:
        existing = (await self._latest_interactions()).get(record.interaction_id)
        if existing is not None:
            return existing
        await self._append_jsonl("interactions.jsonl", _interaction_to_payload(record))
        return record

    async def resolve_agent_interaction(
        self,
        interaction_id: str,
        *,
        status: str,
        response_payload: JSONObject,
        resolved_at: datetime,
    ) -> AgentInteractionRecord:
        _require_non_empty("interaction_id", interaction_id)
        _require_non_empty("status", status)
        _require_json_object("response_payload", response_payload)
        _require_datetime("resolved_at", resolved_at)
        existing = (await self._latest_interactions()).get(interaction_id)
        if existing is None:
            raise KeyError(f"agent interaction not found: {interaction_id}")
        if existing.resolved_at is not None:
            return existing
        updated = AgentInteractionRecord(
            interaction_id=existing.interaction_id,
            run_id=existing.run_id,
            interaction_kind=existing.interaction_kind,
            status=status,
            request_payload=existing.request_payload,
            response_payload=response_payload,
            metadata=existing.metadata,
            created_at=existing.created_at,
            resolved_at=resolved_at,
        )
        await self._append_jsonl("interactions.jsonl", _interaction_to_payload(updated))
        return updated

    async def load_agent_interaction(
        self,
        interaction_id: str,
    ) -> AgentInteractionRecord | None:
        _require_non_empty("interaction_id", interaction_id)
        return (await self._latest_interactions()).get(interaction_id)

    async def list_agent_interactions(
        self,
        run_id: str,
    ) -> list[AgentInteractionRecord]:
        _require_non_empty("run_id", run_id)
        interactions = [
            interaction
            for interaction in (await self._latest_interactions()).values()
            if interaction.run_id == run_id
        ]
        return sorted(
            interactions,
            key=lambda item: (item.created_at, item.interaction_id),
        )

    async def commit_authoritative_uow(self, *args: object, **kwargs: object) -> None:
        self._refuse_authoritative_write()

    async def raise_retention_floor(self, *args: object, **kwargs: object) -> None:
        self._refuse_authoritative_write()

    async def accept_trusted_handoff(self, *args: object, **kwargs: object) -> None:
        self._refuse_authoritative_write()

    def _refuse_authoritative_write(self) -> None:
        raise AuthoritativeWriteRefusedError(
            "JSONLRuntimeStore is a derived export, not an authoritative writer"
        )

    async def _latest_runs(self) -> dict[str, AgentRunRecord]:
        with self._lock:
            return self._latest_runs_sync()

    def _latest_runs_sync(self) -> dict[str, AgentRunRecord]:
        records: dict[str, AgentRunRecord] = {}
        for payload in self._read_jsonl_sync("runs.jsonl"):
            record = _agent_run_from_payload(payload)
            records[record.run_id] = record
        return records

    async def _runtime_events(self) -> list[RuntimeEventRecord]:
        with self._lock:
            return self._runtime_events_sync()

    def _runtime_events_sync(self) -> list[RuntimeEventRecord]:
        return [
            _runtime_event_from_payload(payload)
            for payload in self._read_jsonl_sync("events.jsonl")
        ]

    async def _latest_message_snapshots(
        self,
    ) -> dict[str, RunMessageSnapshotRecord]:
        snapshots: dict[str, RunMessageSnapshotRecord] = {}
        with self._lock:
            for payload in self._read_jsonl_sync("snapshots.jsonl"):
                snapshot = _message_snapshot_from_payload(payload)
                snapshots[snapshot.snapshot_id] = snapshot
        return snapshots

    async def _latest_interactions(self) -> dict[str, AgentInteractionRecord]:
        interactions: dict[str, AgentInteractionRecord] = {}
        with self._lock:
            for payload in self._read_jsonl_sync("interactions.jsonl"):
                interaction = _interaction_from_payload(payload)
                interactions[interaction.interaction_id] = interaction
        return interactions

    async def _append_jsonl(self, filename: str, payload: JSONObject) -> None:
        with self._lock:
            self._append_jsonl_sync(filename, payload)

    def _append_jsonl_sync(self, filename: str, payload: JSONObject) -> None:
        path = self._root / filename
        self._root.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True))
            handle.write("\n")

    def _read_jsonl_sync(self, filename: str) -> list[JSONObject]:
        path = self._root / filename
        if not path.exists():
            return []
        rows: list[JSONObject] = []
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise TypeError(f"{filename}:{line_number} must be a JSON object")
                rows.append(cast(JSONObject, payload))
        return rows
