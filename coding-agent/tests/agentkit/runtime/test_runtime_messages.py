from __future__ import annotations

import pytest

from agentkit.runtime import (
    InMemoryRuntimeMessageBus,
    RuntimeMessage,
    RuntimeMessageCursor,
    RuntimeMessageKind,
)


def test_runtime_message_kinds_cover_pr4_controls() -> None:
    assert {kind.value for kind in RuntimeMessageKind} == {
        "interrupt",
        "user_steer",
        "approval_decision",
        "subagent_message",
        "system_notice",
    }


@pytest.mark.asyncio
async def test_runtime_message_bus_consumes_by_cursor_idempotently() -> None:
    bus = InMemoryRuntimeMessageBus()

    first = await bus.publish(
        RuntimeMessage(
            message_id="msg-1",
            kind=RuntimeMessageKind.USER_STEER,
            payload={"text": "Prefer small steps"},
        )
    )
    second = await bus.publish(
        RuntimeMessage(
            message_id="msg-2",
            kind=RuntimeMessageKind.SYSTEM_NOTICE,
            payload={"text": "Checkpoint restored"},
        )
    )

    assert first.sequence == 1
    assert second.sequence == 2

    start = RuntimeMessageCursor()
    batch = await bus.consume_after(start)
    repeat = await bus.consume_after(start)
    empty = await bus.consume_after(batch.cursor)

    assert [item.message.message_id for item in batch.messages] == ["msg-1", "msg-2"]
    assert batch.cursor.sequence == 2
    assert repeat == batch
    assert empty.messages == ()
    assert empty.cursor == batch.cursor


@pytest.mark.asyncio
async def test_runtime_message_bus_honors_limit() -> None:
    bus = InMemoryRuntimeMessageBus()
    await bus.publish(
        RuntimeMessage(message_id="msg-1", kind=RuntimeMessageKind.USER_STEER)
    )
    await bus.publish(
        RuntimeMessage(message_id="msg-2", kind=RuntimeMessageKind.SYSTEM_NOTICE)
    )

    first = await bus.consume_after(RuntimeMessageCursor(), limit=1)
    second = await bus.consume_after(first.cursor)

    assert [item.message.message_id for item in first.messages] == ["msg-1"]
    assert first.cursor.sequence == 1
    assert [item.message.message_id for item in second.messages] == ["msg-2"]
    assert second.cursor.sequence == 2


@pytest.mark.asyncio
async def test_runtime_message_bus_filters_kinds_with_independent_cursors() -> None:
    bus = InMemoryRuntimeMessageBus()
    await bus.publish(
        RuntimeMessage(
            message_id="msg-approval",
            kind=RuntimeMessageKind.APPROVAL_DECISION,
            payload={"request_id": "req-1", "approved": True},
        )
    )
    await bus.publish(
        RuntimeMessage(
            message_id="msg-steer",
            kind=RuntimeMessageKind.USER_STEER,
            payload={"text": "Prefer short answers"},
        )
    )

    pipeline_batch = await bus.consume_after(
        RuntimeMessageCursor(),
        kinds={RuntimeMessageKind.USER_STEER},
    )
    approval_batch = await bus.consume_after(
        RuntimeMessageCursor(),
        kinds={RuntimeMessageKind.APPROVAL_DECISION},
    )

    assert [item.message.message_id for item in pipeline_batch.messages] == [
        "msg-steer"
    ]
    assert pipeline_batch.cursor.sequence == 2
    assert [item.message.message_id for item in approval_batch.messages] == [
        "msg-approval"
    ]
    assert approval_batch.cursor.sequence == 1


@pytest.mark.asyncio
async def test_runtime_message_bus_rejects_duplicate_message_ids() -> None:
    bus = InMemoryRuntimeMessageBus()
    message = RuntimeMessage(
        message_id="msg-1",
        kind=RuntimeMessageKind.INTERRUPT,
        payload={"reason": "stop"},
    )

    await bus.publish(message)

    with pytest.raises(ValueError, match="duplicate runtime message_id"):
        await bus.publish(message)


def test_runtime_message_rejects_empty_message_id() -> None:
    with pytest.raises(ValueError, match="message_id must be non-empty"):
        RuntimeMessage(
            message_id="",
            kind=RuntimeMessageKind.USER_STEER,
            payload={"text": "invalid"},
        )


def test_runtime_message_rejects_unknown_kind_with_clear_error() -> None:
    with pytest.raises(ValueError, match="unknown runtime message kind: unknown"):
        RuntimeMessage(
            message_id="msg-1",
            kind="unknown",  # pyright: ignore[reportArgumentType]
        )
