import pytest
from agentkit.channel.protocol import Channel


class FakeChannel:
    def __init__(self):
        self._messages = []
        self._subscribers = []

    async def send(self, message: dict) -> None:
        self._messages.append(message)
        for sub in self._subscribers:
            await sub(message)

    async def receive(self) -> dict | None:
        return self._messages.pop(0) if self._messages else None

    def subscribe(self, callback) -> None:
        self._subscribers.append(callback)

    def unsubscribe(self, callback) -> None:
        self._subscribers.remove(callback)


class TestChannelProtocol:
    def test_fake_satisfies_protocol(self):
        ch = FakeChannel()
        assert isinstance(ch, Channel)

    @pytest.mark.asyncio
    async def test_send_and_receive(self):
        ch = FakeChannel()
        await ch.send({"type": "text", "content": "hello"})
        msg = await ch.receive()
        assert msg["content"] == "hello"

    @pytest.mark.asyncio
    async def test_receive_empty(self):
        ch = FakeChannel()
        msg = await ch.receive()
        assert msg is None
