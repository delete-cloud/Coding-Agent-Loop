from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest
from httpx import ASGITransport, AsyncClient

from coding_agent.runtime_store import (
    AgentInteractionRecord,
    AgentRunRecord,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
)
from coding_agent.ui import http_server
from coding_agent.server.http_server import app
from coding_agent.server.session_manager import MockProvider, SessionManager
from coding_agent.server.stores.session_store import InMemorySessionStore


class InMemoryRuntimeStore:
    def __init__(self) -> None:
        self.runs: dict[str, AgentRunRecord] = {}
        self.events: dict[str, RuntimeEventRecord] = {}
        self.events_by_run: dict[str, list[RuntimeEventRecord]] = {}
        self.snapshots: dict[str, RunMessageSnapshotRecord] = {}
        self.interactions: dict[str, AgentInteractionRecord] = {}

    async def create_agent_run(self, record: AgentRunRecord) -> AgentRunRecord:
        self.runs[record.run_id] = record
        return record

    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None:
        return self.runs.get(run_id)

    async def list_agent_runs(self, session_id: str) -> list[AgentRunRecord]:
        return [run for run in self.runs.values() if run.session_id == session_id]

    async def append_runtime_event(
        self,
        record: RuntimeEventRecord,
    ) -> RuntimeEventRecord:
        sequence = len(self.events_by_run.get(record.run_id, [])) + 1
        event = RuntimeEventRecord(
            event_id=record.event_id,
            run_id=record.run_id,
            event_kind=record.event_kind,
            payload=record.payload,
            created_at=record.created_at,
            sequence=record.sequence or sequence,
        )
        self.events[event.event_id] = event
        self.events_by_run.setdefault(event.run_id, []).append(event)
        return event

    async def load_runtime_event(
        self,
        event_id: str,
    ) -> RuntimeEventRecord | None:
        return self.events.get(event_id)

    async def replay_runtime_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> list[RuntimeEventRecord]:
        events = self.events_by_run.get(run_id, [])
        return [
            event
            for event in events
            if event.sequence is not None and event.sequence > after_sequence
        ][:limit]

    async def save_message_snapshot(
        self,
        record: RunMessageSnapshotRecord,
    ) -> RunMessageSnapshotRecord:
        self.snapshots[record.snapshot_id] = record
        return record

    async def load_message_snapshot(
        self,
        snapshot_id: str,
    ) -> RunMessageSnapshotRecord | None:
        return self.snapshots.get(snapshot_id)

    async def create_agent_interaction(
        self,
        record: AgentInteractionRecord,
    ) -> AgentInteractionRecord:
        self.interactions[record.interaction_id] = record
        return record

    async def list_agent_interactions(
        self,
        run_id: str,
    ) -> list[AgentInteractionRecord]:
        return [
            interaction
            for interaction in self.interactions.values()
            if interaction.run_id == run_id
        ]

    async def resolve_agent_interaction(
        self,
        interaction_id: str,
        *,
        status: str,
        response_payload: dict[str, object],
        resolved_at: datetime,
    ) -> AgentInteractionRecord:
        record = self.interactions[interaction_id]
        resolved = AgentInteractionRecord(
            interaction_id=record.interaction_id,
            run_id=record.run_id,
            interaction_kind=record.interaction_kind,
            status=status,
            request_payload=record.request_payload,
            response_payload=cast(dict, response_payload),
            metadata=record.metadata,
            created_at=record.created_at,
            resolved_at=resolved_at,
        )
        self.interactions[interaction_id] = resolved
        return resolved

    async def update_agent_run(
        self,
        run_id: str,
        *,
        status: str,
        ended_at: datetime | None,
        metadata: dict[str, object],
        result: dict[str, object],
        error: str | None,
    ) -> AgentRunRecord:
        current = self.runs[run_id]
        updated = AgentRunRecord(
            run_id=current.run_id,
            session_id=current.session_id,
            tape_id=current.tape_id,
            parent_run_id=current.parent_run_id,
            agent_id=current.agent_id,
            status=status,
            started_at=current.started_at,
            ended_at=ended_at,
            metadata=cast(dict, metadata),
            result=cast(dict, result),
            error=error,
        )
        self.runs[run_id] = updated
        return updated


FORBIDDEN_RENDERED_TEXT: Sequence[str] = (
    "local dogfood readiness check",
    "I'll help you with that request",
    "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
    "command_output",
    "stdout",
    "stderr",
    "DOGFOOD_ENV_SECRET_SENTINEL",
)


@pytest.mark.asyncio
async def test_local_dogfood_run_produces_sanitized_console_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_store = InMemoryRuntimeStore()
    manager = SessionManager(
        store=InMemorySessionStore(),
        runtime_store=runtime_store,
    )
    monkeypatch.setattr(http_server, "session_manager", manager)

    session_id = await manager.create_session(
        repo_path=Path.cwd(),
        provider=MockProvider(),
        provider_name="mock",
        model_name="mock",
        max_steps=1,
    )
    await manager.run_agent(session_id, "local dogfood readiness check")

    session = manager.get_session(session_id)
    run_id = session.current_turn_id
    assert run_id
    run = await manager.load_runtime_run(run_id)
    assert run.session_id == session_id
    assert run.status == "completed"
    assert run.result["stop_reason"] == "no_tool_calls"
    assert isinstance(run.result["steps_taken"], int)
    assert run.result["steps_taken"] >= 0
    assert "final_message" not in run.result

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/healthz", "/readyz"):
            response = await client.get(path)
            assert response.status_code == 200, path

        pages = {
            "/console/sessions": (session_id,),
            "/console/runs": (run_id, "completed"),
            f"/console/runs/{run_id}": ("Run Metadata", "Message Snapshot"),
            f"/console/observability?run_id={run_id}": ("Trace Correlation", run_id),
            "/console/release": ("Health / Readiness", "durable-runtime-smoke"),
        }
        for path, expected_text in pages.items():
            response = await client.get(path)

            assert response.status_code == 200, path
            for text in expected_text:
                assert text in response.text, path
            for forbidden in FORBIDDEN_RENDERED_TEXT:
                assert forbidden not in response.text, path
