from __future__ import annotations

from datetime import UTC, datetime

import pytest

from coding_agent.events import RuntimeEventReplayService
from coding_agent.runtime_store import RuntimeEventRecord


class FakeRuntimeEventStore:
    def __init__(self, events: list[RuntimeEventRecord]) -> None:
        self.events = events
        self.replay_calls: list[tuple[str, int, int]] = []

    async def load_runtime_event(
        self,
        event_id: str,
    ) -> RuntimeEventRecord | None:
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
        self.replay_calls.append((run_id, after_sequence, limit))
        events = [
            event
            for event in self.events
            if event.run_id == run_id and (event.sequence or 0) > after_sequence
        ]
        return sorted(events, key=lambda event: event.sequence or 0)[:limit]


@pytest.mark.asyncio
async def test_replay_runtime_events_uses_runtime_event_cursor() -> None:
    store = FakeRuntimeEventStore(
        [
            _event("event-1", sequence=1),
            _event("event-2", sequence=2),
        ]
    )

    events = await RuntimeEventReplayService(store).replay_runtime_events(
        "run-1",
        last_event_id="event-1",
        limit=10,
    )

    assert [event.event_id for event in events] == ["event-2"]
    assert store.replay_calls == [("run-1", 1, 10)]


@pytest.mark.asyncio
async def test_replay_runtime_events_rejects_cursor_from_other_run() -> None:
    store = FakeRuntimeEventStore([_event("event-other", run_id="run-other")])

    with pytest.raises(KeyError, match="runtime event not found: event-other"):
        await RuntimeEventReplayService(store).replay_runtime_events(
            "run-1",
            last_event_id="event-other",
        )


@pytest.mark.asyncio
async def test_replay_runtime_events_rejects_unsequenced_cursor() -> None:
    store = FakeRuntimeEventStore([_event("event-1", sequence=None)])

    with pytest.raises(RuntimeError, match="runtime event has no replay sequence"):
        await RuntimeEventReplayService(store).replay_runtime_events(
            "run-1",
            last_event_id="event-1",
        )


@pytest.mark.asyncio
async def test_replay_display_events_scans_past_internal_runtime_events() -> None:
    store = FakeRuntimeEventStore(
        [
            _stream_event("event-1", sequence=1, content="first"),
            _event("event-2", sequence=2, event_kind="model_request_started"),
            _event("event-3", sequence=3, event_kind="model_response_started"),
            _event("event-4", sequence=4, event_kind="tool_call_started"),
            _event("event-5", sequence=5, event_kind="tool_call_finished"),
            _stream_event("event-6", sequence=6, content="second"),
        ]
    )

    display_events = await RuntimeEventReplayService(store).replay_display_events(
        "run-1",
        last_event_id="event-1",
        limit=1,
    )

    assert [event.source_event_id for event in display_events] == ["event-6"]
    assert [event.payload["content"] for event in display_events] == ["second"]
    assert store.replay_calls == [
        ("run-1", 1, 1),
        ("run-1", 2, 1),
        ("run-1", 3, 1),
        ("run-1", 4, 1),
        ("run-1", 5, 1),
    ]


@pytest.mark.asyncio
async def test_replay_display_events_rejects_non_positive_limit() -> None:
    service = RuntimeEventReplayService(FakeRuntimeEventStore([]))

    with pytest.raises(ValueError, match="limit must be positive"):
        await service.replay_display_events("run-1", limit=0)


def _event(
    event_id: str,
    *,
    run_id: str = "run-1",
    sequence: int | None = 1,
    event_kind: str = "model_request_started",
) -> RuntimeEventRecord:
    return RuntimeEventRecord(
        event_id=event_id,
        run_id=run_id,
        event_kind=event_kind,
        payload={"event_id": event_id},
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        sequence=sequence,
    )


def _stream_event(
    event_id: str,
    *,
    sequence: int,
    content: str,
) -> RuntimeEventRecord:
    return RuntimeEventRecord(
        event_id=event_id,
        run_id="run-1",
        event_kind="wire.StreamDelta",
        payload={
            "message_type": "StreamDelta",
            "message": {"content": content, "role": "assistant"},
        },
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        sequence=sequence,
    )
