from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from coding_agent.events.connected_chat import ChatEvent
from coding_agent.server.http.events import ChatFollowBridge, StreamControl


def event(seq: int) -> ChatEvent:
    return ChatEvent(
        source_event_id=f"event-{seq}",
        session_seq=str(seq),
        session_id="session-01",
        run_id=None,
        kind="assistant_message",
        created_at=datetime(2026, 8, 24, tzinfo=UTC),
        payload={"text": str(seq)},
    )


class Harness:
    def __init__(self) -> None:
        self.registered: Any = None
        self.unregistered = 0
        self.head = 2
        self.records = [event(1), event(2)]
        self.registered_barrier = asyncio.Event()
        self.capture_barrier = asyncio.Event()
        self.owner = True
        self.lose_ownership_on_register = False
        self.capture_calls = 0

    async def register(self, subscriber: Any) -> None:
        self.registered = subscriber
        if self.lose_ownership_on_register:
            self.owner = False
        self.registered_barrier.set()

    async def capture(self) -> str:
        self.capture_calls += 1
        await self.capture_barrier.wait()
        return str(self.head)

    async def replay(self, after: str, high_water: str) -> tuple[ChatEvent, ...]:
        return tuple(
            item
            for item in self.records
            if int(after) < int(item.session_seq) <= int(high_water)
        )

    async def verify(self) -> bool:
        return self.owner

    async def unregister(self, subscriber: Any) -> None:
        assert subscriber is self.registered
        self.unregistered += 1


def bridge(harness: Harness, *, queue_size: int = 4) -> ChatFollowBridge:
    return ChatFollowBridge(
        session_id="session-01",
        projection_epoch="7",
        register=harness.register,
        capture_high_water=harness.capture,
        replay=harness.replay,
        verify_ownership=harness.verify,
        unregister=harness.unregister,
        queue_size=queue_size,
    )


@pytest.mark.asyncio
async def test_pm0021_registration_publication_race() -> None:
    harness = Harness()
    stream = bridge(harness).follow(after_seq="0")
    first_task = asyncio.create_task(anext(stream))
    await harness.registered_barrier.wait()
    harness.records.append(event(3))
    harness.head = 3
    harness.registered.publish(event(3))
    harness.registered.publish(event(4))
    harness.capture_barrier.set()

    visible = [
        await first_task,
        await anext(stream),
        await anext(stream),
        await anext(stream),
    ]
    assert [item.source_event_id for item in visible] == [
        "event-1",
        "event-2",
        "event-3",
        "event-4",
    ]
    assert [item.session_seq for item in visible] == ["1", "2", "3", "4"]
    await stream.aclose()
    assert harness.unregistered == 1


@pytest.mark.asyncio
async def test_follow_overflow_requires_replay() -> None:
    harness = Harness()
    harness.capture_barrier.set()
    stream = bridge(harness, queue_size=1).follow(after_seq="2")
    next_task = asyncio.create_task(anext(stream))
    await harness.registered_barrier.wait()
    harness.registered.publish(event(3))
    harness.registered.publish(event(4))

    control = await next_task
    assert control == StreamControl(
        kind="replay_required",
        reason="subscriber_queue_overflow",
        cursor=bridge(harness).cursor("2", "2"),
    )
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
async def test_follow_ownership_loss_requires_replay() -> None:
    harness = Harness()
    harness.capture_barrier.set()
    stream = bridge(harness).follow(after_seq="2")
    next_task = asyncio.create_task(anext(stream))
    await harness.registered_barrier.wait()
    harness.owner = False
    harness.registered.publish(event(3))

    control = await next_task
    assert isinstance(control, StreamControl)
    assert control.reason == "ownership_lost"
    assert control.cursor == bridge(harness).cursor("2", "2")
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert harness.unregistered == 1


@pytest.mark.asyncio
async def test_follow_sequence_loss_requires_replay() -> None:
    harness = Harness()
    harness.capture_barrier.set()
    stream = bridge(harness).follow(after_seq="2")
    next_task = asyncio.create_task(anext(stream))
    await harness.registered_barrier.wait()
    harness.registered.publish(event(4))

    control = await next_task
    assert isinstance(control, StreamControl)
    assert control.reason == "sequence_loss"
    assert control.cursor == bridge(harness).cursor("2", "2")
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert harness.unregistered == 1


@pytest.mark.asyncio
async def test_follow_recovers_projected_event_across_non_chat_sequence_gap() -> None:
    harness = Harness()
    harness.capture_barrier.set()
    stream = bridge(harness).follow(after_seq="2")
    next_task = asyncio.create_task(anext(stream))
    await harness.registered_barrier.wait()
    harness.records.append(event(4))
    harness.registered.publish(event(4))

    recovered = await next_task
    assert recovered == event(4)

    harness.owner = False
    harness.registered.publish(event(6))
    control = await anext(stream)
    assert control == StreamControl(
        kind="replay_required",
        reason="ownership_lost",
        cursor=bridge(harness).cursor("4", "4"),
    )
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert harness.unregistered == 1


@pytest.mark.asyncio
async def test_pm0022_ownership_revalidation_race() -> None:
    harness = Harness()
    harness.lose_ownership_on_register = True
    harness.capture_barrier.set()
    stream = bridge(harness).follow(after_seq="2")
    next_task = asyncio.create_task(anext(stream))
    await harness.registered_barrier.wait()
    harness.registered.publish(event(3))

    control = await next_task

    assert control == StreamControl(
        kind="replay_required",
        reason="ownership_lost",
        cursor=bridge(harness).cursor("2", "2"),
    )
    assert harness.capture_calls == 0
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert harness.unregistered == 1


@pytest.mark.asyncio
async def test_pm0023_idempotent_teardown_race() -> None:
    harness = Harness()
    harness.capture_barrier.set()
    stream = bridge(harness).follow(after_seq="2")
    next_task = asyncio.create_task(anext(stream))
    await harness.registered_barrier.wait()
    next_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await next_task
    await stream.aclose()
    assert harness.unregistered == 1
