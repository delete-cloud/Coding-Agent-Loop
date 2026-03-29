import pytest
from agentkit.providers.protocol import LLMProvider
from agentkit.providers.models import StreamEvent, ToolCallEvent, TextEvent, DoneEvent


class FakeLLM:
    @property
    def model_name(self) -> str:
        return "fake-model"

    @property
    def max_context_size(self) -> int:
        return 128000

    async def stream(self, messages, tools=None, **kwargs):
        yield TextEvent(text="Hello")
        yield DoneEvent()


class TestLLMProviderProtocol:
    def test_fake_satisfies_protocol(self):
        llm = FakeLLM()
        assert isinstance(llm, LLMProvider)

    def test_model_name(self):
        llm = FakeLLM()
        assert llm.model_name == "fake-model"

    def test_max_context_size(self):
        llm = FakeLLM()
        assert llm.max_context_size == 128000


class TestStreamEvents:
    def test_text_event(self):
        e = TextEvent(text="hello")
        assert e.kind == "text"
        assert e.text == "hello"

    def test_tool_call_event(self):
        e = ToolCallEvent(
            tool_call_id="tc_1",
            name="bash",
            arguments={"cmd": "ls"},
        )
        assert e.kind == "tool_call"
        assert e.name == "bash"

    def test_done_event(self):
        e = DoneEvent()
        assert e.kind == "done"

    def test_all_events_are_stream_events(self):
        for cls in (TextEvent, ToolCallEvent, DoneEvent):
            if cls is DoneEvent:
                e = cls()
            elif cls is TextEvent:
                e = cls(text="x")
            else:
                e = cls(tool_call_id="x", name="x", arguments={})
            assert isinstance(e, StreamEvent)
