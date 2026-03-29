import pytest
import asyncio
from agentkit.channel.local import LocalChannel


class TestLocalChannel:
    @pytest.mark.asyncio
    async def test_send_and_receive(self):
        ch = LocalChannel()
        await ch.send({"type": "text", "content": "hello"})
        msg = await ch.receive()
        assert msg is not None
        assert msg["content"] == "hello"

    @pytest.mark.asyncio
    async def test_receive_empty_returns_none(self):
        ch = LocalChannel()
        msg = await ch.receive()
        assert msg is None

    @pytest.mark.asyncio
    async def test_fifo_order(self):
        ch = LocalChannel()
        await ch.send({"n": 1})
        await ch.send({"n": 2})
        await ch.send({"n": 3})
        assert (await ch.receive())["n"] == 1
        assert (await ch.receive())["n"] == 2
        assert (await ch.receive())["n"] == 3

    @pytest.mark.asyncio
    async def test_subscriber_called(self):
        ch = LocalChannel()
        received = []

        async def on_msg(msg):
            received.append(msg)

        ch.subscribe(on_msg)
        await ch.send({"content": "test"})
        assert len(received) == 1
        assert received[0]["content"] == "test"

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        ch = LocalChannel()
        received = []

        async def on_msg(msg):
            received.append(msg)

        ch.subscribe(on_msg)
        ch.unsubscribe(on_msg)
        await ch.send({"content": "ignored"})
        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self):
        ch = LocalChannel()
        a, b = [], []

        async def sub_a(msg):
            a.append(msg)

        async def sub_b(msg):
            b.append(msg)

        ch.subscribe(sub_a)
        ch.subscribe(sub_b)
        await ch.send({"n": 1})
        assert len(a) == 1
        assert len(b) == 1
