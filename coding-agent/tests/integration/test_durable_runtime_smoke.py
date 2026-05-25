from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from agentkit.observability import SpanRecord
from agentkit.runtime.context import AgentRunContext
from agentkit.storage.pg import PGTapeStore
from agentkit.tape.tape import Tape
from coding_agent.adapter_types import StopReason, TurnOutcome
from coding_agent.observability import OtlpHttpObservationSink
from coding_agent.runtime_store import (
    AgentInteractionRecord,
    AgentRunRecord,
    JSONObject,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
)
from coding_agent.server.session_manager import SessionManager
from coding_agent.server.stores.session_store import InMemorySessionStore
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    CompletionStatus,
    StreamDelta,
    ToolCallDelta,
    TurnEnd,
)
import coding_agent.server.http_server as http_server


class _SmokeRuntimeStore:
    def __init__(self) -> None:
        self.created: list[AgentRunRecord] = []
        self.updated: list[AgentRunRecord] = []
        self.runs: dict[str, AgentRunRecord] = {}
        self.events: list[RuntimeEventRecord] = []
        self.snapshots: dict[str, RunMessageSnapshotRecord] = {}
        self.interactions: dict[str, AgentInteractionRecord] = {}

    async def create_agent_run(self, record: AgentRunRecord) -> AgentRunRecord:
        self.created.append(record)
        self.runs[record.run_id] = record
        return record

    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None:
        return self.runs.get(run_id)

    async def list_agent_runs(self, session_id: str) -> list[AgentRunRecord]:
        return [
            record for record in self.runs.values() if record.session_id == session_id
        ]

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
        existing = self.runs[run_id]
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
        )
        self.runs[run_id] = updated
        self.updated.append(updated)
        return updated

    async def append_runtime_event(
        self, record: RuntimeEventRecord
    ) -> RuntimeEventRecord:
        stored = RuntimeEventRecord(
            sequence=len(self.events) + 1,
            event_id=record.event_id,
            run_id=record.run_id,
            event_kind=record.event_kind,
            payload=record.payload,
            created_at=record.created_at,
        )
        self.events.append(stored)
        return stored

    async def load_runtime_event(self, event_id: str) -> RuntimeEventRecord | None:
        for event in self.events:
            if event.event_id == event_id:
                return event
        return None

    async def replay_runtime_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> list[RuntimeEventRecord]:
        return [
            event
            for event in self.events
            if event.run_id == run_id
            and event.sequence is not None
            and event.sequence > after_sequence
        ][:limit]

    async def save_message_snapshot(
        self, record: RunMessageSnapshotRecord
    ) -> RunMessageSnapshotRecord:
        self.snapshots[record.snapshot_id] = record
        return record

    async def load_message_snapshot(
        self, snapshot_id: str
    ) -> RunMessageSnapshotRecord | None:
        return self.snapshots.get(snapshot_id)

    async def create_agent_interaction(
        self, record: AgentInteractionRecord
    ) -> AgentInteractionRecord:
        existing = self.interactions.get(record.interaction_id)
        if existing is not None:
            return existing
        self.interactions[record.interaction_id] = record
        return record

    async def resolve_agent_interaction(
        self,
        interaction_id: str,
        *,
        status: str,
        response_payload: JSONObject,
        resolved_at: datetime,
    ) -> AgentInteractionRecord:
        existing = self.interactions[interaction_id]
        if existing.resolved_at is not None:
            return existing
        resolved = AgentInteractionRecord(
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
        self.interactions[interaction_id] = resolved
        return resolved


def _create_agent(**kwargs: object) -> tuple[object, object]:
    session_id = kwargs["session_id_override"]
    run_id = kwargs["run_id_override"]
    environment = kwargs["environment"]
    if not isinstance(session_id, str):
        raise TypeError("session_id_override must be a string")
    if not isinstance(run_id, str):
        raise TypeError("run_id_override must be a string")
    tape = kwargs.get("tape")
    if tape is not None and not isinstance(tape, Tape):
        raise TypeError("tape must be a Tape or None")
    effective_tape = tape or Tape(tape_id=f"{session_id}-tape")
    pipeline = SimpleNamespace(
        _registry=SimpleNamespace(
            get=lambda _: SimpleNamespace(_instance=None),
        )
    )
    ctx = SimpleNamespace(
        session_id=session_id,
        config={},
        tape=effective_tape,
        messages=[],
        run_context=AgentRunContext(
            session_id=session_id,
            run_id=run_id,
            agent_id=None,
            environment=cast(Any, environment),
            trace_metadata={"checkpoint_id": "checkpoint-smoke"},
        ),
    )
    return pipeline, ctx


def _manager(runtime_store: _SmokeRuntimeStore) -> SessionManager:
    return SessionManager(
        store=InMemorySessionStore(),
        runtime_store=runtime_store,
        create_agent_fn=_create_agent,
    )


def _install_adapter(
    monkeypatch: pytest.MonkeyPatch,
    run_turn: Callable[[object, str], Awaitable[TurnOutcome]],
) -> None:
    class SmokeAdapter:
        def __init__(self, pipeline: object, ctx: object, consumer: object) -> None:
            del pipeline
            self.ctx = ctx
            self.consumer = consumer

        def set_consumer(self, consumer: object) -> None:
            self.consumer = consumer

        async def run_turn(self, prompt: str) -> TurnOutcome:
            return await run_turn(self, prompt)

    monkeypatch.setattr(
        "coding_agent.server.session_manager.PipelineAdapter",
        SmokeAdapter,
    )


async def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout_seconds: float = 1.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


def _statuses_for(store: _SmokeRuntimeStore, run_id: str) -> list[str]:
    return [record.status for record in store.updated if record.run_id == run_id]


@pytest.fixture(autouse=True)
async def _clean_http_server_state() -> None:
    http_server.session_manager.configure_owner_leases(
        owner_store=None,
        owner_id=None,
        fencing_token=None,
    )
    http_server.session_manager.configure_workspace_metadata_store(None)
    http_server.session_manager.configure_runtime_store(None)
    http_server.session_manager.clear_sessions()
    http_server.limiter.reset()
    yield
    http_server.session_manager.configure_runtime_store(None)
    http_server.session_manager.clear_sessions()
    http_server.limiter.reset()


@pytest.mark.asyncio
async def test_smoke_normal_and_failed_runs_persist_lifecycle_events_and_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _SmokeRuntimeStore()
    manager = _manager(store)

    async def normal_turn(adapter: object, prompt: str) -> TurnOutcome:
        ctx = adapter.ctx
        run_id = ctx.run_context.run_id
        ctx.messages = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": "normal smoke complete"},
        ]
        await adapter.consumer.emit(
            StreamDelta(
                session_id=ctx.session_id,
                agent_id="",
                content="normal smoke complete",
            )
        )
        await adapter.consumer.emit(
            TurnEnd(
                session_id=ctx.session_id,
                agent_id="",
                turn_id=run_id,
                completion_status=CompletionStatus.COMPLETED,
            )
        )
        return TurnOutcome(
            stop_reason=StopReason.NO_TOOL_CALLS,
            final_message="normal smoke complete",
            steps_taken=1,
        )

    _install_adapter(monkeypatch, normal_turn)
    normal_session_id = await manager.create_session()
    await manager.run_agent(normal_session_id, "normal smoke")
    normal_run_id = store.created[0].run_id

    assert store.created[0].status == "queued"
    assert _statuses_for(store, normal_run_id) == ["running", "completed"]
    assert store.runs[normal_run_id].tape_id == f"{normal_session_id}-tape"
    assert store.runs[normal_run_id].result == {
        "stop_reason": "no_tool_calls",
        "steps_taken": 1,
    }
    assert [
        event.event_kind for event in store.events if event.run_id == normal_run_id
    ] == ["wire.StreamDelta", "wire.TurnEnd"]
    assert store.snapshots[f"{normal_run_id}:latest"].metadata == {
        "session_id": normal_session_id,
        "tape_id": f"{normal_session_id}-tape",
        "message_count": 2,
        "snapshot_kind": "latest_context",
    }

    async def failed_turn(adapter: object, prompt: str) -> TurnOutcome:
        ctx = adapter.ctx
        run_id = ctx.run_context.run_id
        ctx.messages = [{"role": "user", "content": prompt}]
        await adapter.consumer.emit(
            TurnEnd(
                session_id=ctx.session_id,
                agent_id="",
                turn_id=run_id,
                completion_status=CompletionStatus.ERROR,
            )
        )
        return TurnOutcome(
            stop_reason=StopReason.ERROR,
            steps_taken=1,
            error="model failed",
        )

    _install_adapter(monkeypatch, failed_turn)
    failed_session_id = await manager.create_session()
    await manager.run_agent(failed_session_id, "failed smoke")
    failed_run_id = store.created[1].run_id

    assert _statuses_for(store, failed_run_id) == ["running", "failed"]
    assert store.runs[failed_run_id].error == "model failed"
    assert store.runs[failed_run_id].result == {
        "stop_reason": "error",
        "steps_taken": 1,
    }
    assert store.snapshots[f"{failed_run_id}:latest"].metadata["session_id"] == (
        failed_session_id
    )


@pytest.mark.asyncio
async def test_smoke_approval_run_persists_request_decision_and_wire_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _SmokeRuntimeStore()
    manager = _manager(store)

    async def approval_turn(adapter: object, prompt: str) -> TurnOutcome:
        ctx = adapter.ctx
        run_id = ctx.run_context.run_id
        request = ApprovalRequest(
            session_id=ctx.session_id,
            agent_id="",
            request_id="req-smoke",
            tool_call=ToolCallDelta(
                session_id=ctx.session_id,
                agent_id="",
                tool_name="shell_execute",
                arguments={"cmd": "echo smoke"},
                call_id="tool-smoke",
            ),
            timeout_seconds=5,
        )
        response: ApprovalResponse = await adapter.consumer.request_approval(request)
        ctx.messages = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": f"approved={response.approved}"},
        ]
        await adapter.consumer.emit(
            TurnEnd(
                session_id=ctx.session_id,
                agent_id="",
                turn_id=run_id,
                completion_status=CompletionStatus.COMPLETED,
            )
        )
        return TurnOutcome(
            stop_reason=StopReason.NO_TOOL_CALLS,
            final_message="approval smoke complete",
            steps_taken=1,
        )

    _install_adapter(monkeypatch, approval_turn)
    session_id = await manager.create_session()
    run_task = asyncio.create_task(manager.run_agent(session_id, "approval smoke"))
    await _wait_until(lambda: bool(store.interactions))

    response = await manager.submit_approval_response(
        session_id=session_id,
        request_id="req-smoke",
        approved=True,
        feedback="approved by smoke",
    )
    await run_task

    run_id = store.created[0].run_id
    interaction = store.interactions[f"{run_id}:approval:req-smoke"]
    assert response is not None
    assert response.approved is True
    assert interaction.status == "approved"
    assert interaction.metadata == {
        "session_id": session_id,
        "request_id": "req-smoke",
        "tool_call_id": "tool-smoke",
        "tool_name": "shell_execute",
    }
    assert interaction.response_payload["approved"] is True
    assert [event.event_kind for event in store.events if event.run_id == run_id] == [
        "wire.ApprovalRequest",
        "wire.TurnEnd",
    ]
    assert _statuses_for(store, run_id) == ["running", "completed"]


@pytest.mark.asyncio
async def test_smoke_runtime_replay_http_endpoints_return_run_snapshot_and_events() -> (
    None
):
    store = _SmokeRuntimeStore()
    http_server.session_manager.configure_runtime_store(store)
    session_id = await http_server.session_manager.create_session()
    run_id = "run-smoke-replay"
    started_at = datetime(2026, 5, 18, 1, 2, 3, tzinfo=UTC)

    await store.create_agent_run(
        AgentRunRecord(
            run_id=run_id,
            session_id=session_id,
            tape_id="tape-smoke-replay",
            parent_run_id=None,
            agent_id=None,
            status="completed",
            started_at=started_at,
            ended_at=datetime(2026, 5, 18, 1, 3, 0, tzinfo=UTC),
            metadata={"provider_name": "smoke"},
            result={"stop_reason": "no_tool_calls"},
            error=None,
        )
    )
    await store.save_message_snapshot(
        RunMessageSnapshotRecord(
            snapshot_id=f"{run_id}:latest",
            run_id=run_id,
            messages=[{"role": "assistant", "content": "snapshot"}],
            metadata={"snapshot_kind": "latest_context"},
            created_at=datetime(2026, 5, 18, 1, 2, 30, tzinfo=UTC),
        )
    )
    first_event = await store.append_runtime_event(
        RuntimeEventRecord(
            event_id="event-smoke-1",
            run_id=run_id,
            event_kind="wire.StreamDelta",
            payload={"message_type": "StreamDelta"},
            created_at=datetime(2026, 5, 18, 1, 2, 31, tzinfo=UTC),
        )
    )
    await store.append_runtime_event(
        RuntimeEventRecord(
            event_id="event-smoke-2",
            run_id=run_id,
            event_kind="wire.TurnEnd",
            payload={"message_type": "TurnEnd"},
            created_at=datetime(2026, 5, 18, 1, 2, 32, tzinfo=UTC),
        )
    )

    async with AsyncClient(
        transport=ASGITransport(app=http_server.app),
        base_url="http://test",
    ) as client:
        run_response = await client.get(f"/runs/{run_id}")
        snapshot_response = await client.get(f"/runs/{run_id}/message-snapshot")
        events_response = await client.get(
            f"/runs/{run_id}/events",
            params={"last_event_id": first_event.event_id},
        )

    assert run_response.status_code == 200
    assert run_response.json()["status"] == "completed"
    assert run_response.json()["tape_id"] == "tape-smoke-replay"
    assert snapshot_response.status_code == 200
    assert snapshot_response.json()["snapshot_id"] == f"{run_id}:latest"
    assert events_response.status_code == 200
    assert events_response.json()["events"] == [
        {
            "sequence": 2,
            "event_id": "event-smoke-2",
            "run_id": run_id,
            "event_kind": "wire.TurnEnd",
            "payload": {"message_type": "TurnEnd"},
            "created_at": "2026-05-18T01:02:32Z",
        }
    ]


class _RecordingTransport:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200)


def _first_span(request: httpx.Request) -> dict[str, object]:
    payload = json.loads(request.read().decode())
    resource_spans = payload["resourceSpans"]
    assert isinstance(resource_spans, list)
    scope_spans = resource_spans[0]["scopeSpans"]
    assert isinstance(scope_spans, list)
    spans = scope_spans[0]["spans"]
    assert isinstance(spans, list)
    span = spans[0]
    assert isinstance(span, dict)
    return span


def _span_attribute_values(span: dict[str, object]) -> dict[str, object]:
    attributes = span["attributes"]
    assert isinstance(attributes, list)
    values: dict[str, object] = {}
    for item in attributes:
        assert isinstance(item, dict)
        key = item["key"]
        value = item["value"]
        assert isinstance(key, str)
        values[key] = value
    return values


def test_smoke_langfuse_otlp_correlation_exports_safe_runtime_ids_only() -> None:
    transport = _RecordingTransport()
    encoded = base64.b64encode(b"pk-smoke:sk-smoke").decode("ascii")
    sink = OtlpHttpObservationSink(
        endpoint="https://cloud.langfuse.com/api/public/otel",
        headers={"authorization": f"Basic {encoded}"},
        client=httpx.Client(transport=httpx.MockTransport(transport.handler)),
    )

    sink.record_span(
        SpanRecord(
            name="runtime.stage.model_generate",
            status="ok",
            attributes={
                "session_id": "session-smoke",
                "run_id": "run-smoke",
                "turn_id": "run-smoke",
                "tape_id": "tape-smoke",
                "tool_call_id": "tool-smoke",
                "interaction_id": "interaction-smoke",
                "event_id": "event-smoke",
                "checkpoint_id": "checkpoint-smoke",
                "prompt": "raw prompt must not export",
                "message": "raw message must not export",
                "result": "raw result must not export",
                "secret": "raw secret must not export",
                "text": "raw text must not export",
            },
            start_time=1.0,
            end_time=2.0,
        )
    )

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert str(request.url) == "https://cloud.langfuse.com/api/public/otel/v1/traces"
    assert request.headers["authorization"] == f"Basic {encoded}"
    body = request.read().decode()
    assert "raw prompt must not export" not in body
    assert "raw message must not export" not in body
    assert "raw result must not export" not in body
    assert "raw secret must not export" not in body
    assert "raw text must not export" not in body

    values = _span_attribute_values(_first_span(request))
    assert values["turn_id"] == {"stringValue": "run-smoke"}
    assert values["tape_id"] == {"stringValue": "tape-smoke"}
    assert values["tool_call_id"] == {"stringValue": "tool-smoke"}
    assert values["interaction_id"] == {"stringValue": "interaction-smoke"}
    assert values["event_id"] == {"stringValue": "event-smoke"}
    assert values["checkpoint_id"] == {"stringValue": "checkpoint-smoke"}
    assert {"prompt", "message", "result", "secret", "text"}.isdisjoint(values)


class _TapeDebugConnection:
    def __init__(self) -> None:
        self.tapes: dict[str, list[dict[str, object]]] = {}

    async def execute(self, query: str, *args: object) -> str:
        if "INSERT INTO agent_tapes" not in query:
            return "OK"
        tape_id, payload_values = args
        if not isinstance(tape_id, str) or not isinstance(payload_values, list):
            raise TypeError("invalid fake tape insert")
        rows = self.tapes.setdefault(tape_id, [])
        for payload in payload_values:
            if not isinstance(payload, str):
                raise TypeError("fake tape payload must be JSON text")
            rows.append({"seq": len(rows), "entry": json.loads(payload)})
        return "INSERT 0"

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        del query
        (tape_id,) = args
        if not isinstance(tape_id, str):
            raise TypeError("tape_id must be a string")
        rows = self.tapes.get(tape_id, [])
        if not rows:
            return None
        seqs = [cast(int, row["seq"]) for row in rows]
        return {
            "tape_id": tape_id,
            "entry_count": len(rows),
            "first_seq": min(seqs),
            "last_seq": max(seqs),
        }

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        if "SELECT tape_id, seq, entry" not in query:
            return []
        tape_id, kind, run_id, tool_call_id, anchor_type, limit = args
        if not isinstance(limit, int):
            raise TypeError("limit must be an int")
        matches: list[dict[str, object]] = []
        for current_tape_id, rows in sorted(self.tapes.items()):
            if tape_id is not None and current_tape_id != tape_id:
                continue
            for row in rows:
                entry = row["entry"]
                if not isinstance(entry, dict):
                    raise TypeError("entry must be a dict")
                if kind is not None and entry.get("kind") != kind:
                    continue
                if (
                    run_id is not None
                    and _nested_entry_value(entry, "run_id") != run_id
                ):
                    continue
                if (
                    tool_call_id is not None
                    and _nested_entry_value(entry, "tool_call_id") != tool_call_id
                ):
                    continue
                if anchor_type is not None and _entry_anchor_type(entry) != anchor_type:
                    continue
                matches.append(
                    {
                        "tape_id": current_tape_id,
                        "seq": row["seq"],
                        "entry": entry,
                    }
                )
        return matches[:limit]


def _nested_entry_value(entry: dict[object, object], field: str) -> str | None:
    for parent_name in ("meta", "payload"):
        parent = entry.get(parent_name)
        if isinstance(parent, dict):
            value = parent.get(field)
            if isinstance(value, str):
                return value
    return None


def _entry_anchor_type(entry: dict[object, object]) -> str | None:
    top_level = entry.get("anchor_type")
    if isinstance(top_level, str):
        return top_level
    meta = entry.get("meta")
    if isinstance(meta, dict):
        value = meta.get("anchor_type")
        if isinstance(value, str):
            return value
    return None


class _TapeDebugPGPool:
    def __init__(self) -> None:
        self.connection = _TapeDebugConnection()

    async def get_pool(self) -> _TapeDebugConnection:
        return self.connection


@pytest.mark.asyncio
async def test_smoke_pg_tape_debug_info_and_search_filters() -> None:
    store = PGTapeStore(pool=cast(Any, _TapeDebugPGPool()))
    await store.save(
        "tape-smoke-debug",
        [
            {"kind": "message", "payload": {"run_id": "run-smoke"}},
            {
                "kind": "tool_call",
                "payload": {
                    "run_id": "run-smoke",
                    "tool_call_id": "tool-smoke",
                },
            },
            {
                "kind": "tool_result",
                "payload": {"tool_call_id": "tool-smoke"},
                "meta": {"run_id": "run-smoke"},
            },
            {
                "kind": "anchor",
                "payload": {"summary": "folded"},
                "meta": {"anchor_type": "handoff"},
            },
        ],
    )

    info = await store.info("tape-smoke-debug")
    tool_results = await store.search(
        tape_id="tape-smoke-debug",
        run_id="run-smoke",
        tool_call_id="tool-smoke",
    )
    anchors = await store.search(anchor_type="handoff")

    assert info is not None
    assert (info.tape_id, info.entry_count, info.first_seq, info.last_seq) == (
        "tape-smoke-debug",
        4,
        0,
        3,
    )
    assert [(item.seq, item.entry["kind"]) for item in tool_results] == [
        (1, "tool_call"),
        (2, "tool_result"),
    ]
    assert [(item.tape_id, item.seq) for item in anchors] == [("tape-smoke-debug", 3)]


def test_durable_runtime_smoke_docs_cover_required_scenarios() -> None:
    smoke_doc = Path("docs/durable_runtime/SMOKE.md").read_text()
    progress_doc = Path("docs/durable_runtime/GOAL_PROGRESS.md").read_text()

    for phrase in (
        "normal run",
        "failed run",
        "approval run",
        "runtime replay",
        "Langfuse/OTLP correlation",
        "tape debug",
    ):
        assert phrase in smoke_doc
    assert "| G11 | Complete |" in progress_doc
