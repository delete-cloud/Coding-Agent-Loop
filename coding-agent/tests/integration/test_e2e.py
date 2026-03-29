"""End-to-end integration test — verify all layers wire together."""

import pytest
from pathlib import Path
from agentkit.runtime.pipeline import PipelineContext
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry


class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_full_pipeline_mount_and_turn(self, tmp_path):
        from unittest.mock import AsyncMock
        from coding_agent.__main__ import create_agent
        from agentkit.providers.models import TextEvent, DoneEvent

        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path,
            api_key="sk-test",
        )

        async def mock_stream(messages, tools=None, **kwargs):
            yield TextEvent(text="Hello from mock")
            yield DoneEvent()

        mock_provider = AsyncMock()
        mock_provider.stream = mock_stream

        llm_plugin = pipeline._registry.get("llm_provider")
        llm_plugin._instance = mock_provider

        await pipeline.mount(ctx)
        assert len(ctx.plugin_states) > 0

        ctx.tape.append(
            Entry(
                kind="message",
                payload={"role": "user", "content": "hello"},
            )
        )

        result = await pipeline.run_turn(ctx)
        assert result is not None

    @pytest.mark.asyncio
    async def test_tool_registration_complete(self, tmp_path):
        from coding_agent.__main__ import create_agent

        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path,
            api_key="sk-test",
        )

        tool_lists = pipeline._runtime.call_many("get_tools")
        all_tools = []
        for tl in tool_lists:
            if isinstance(tl, list):
                all_tools.extend(tl)

        tool_names = {t.name for t in all_tools}
        assert "file_read" in tool_names
        assert "file_write" in tool_names
        assert "bash_run" in tool_names
        assert "grep_search" in tool_names

    @pytest.mark.asyncio
    async def test_approval_directive_flow(self, tmp_path):
        from coding_agent.__main__ import create_agent
        from agentkit.directive.types import Approve

        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path,
            api_key="sk-test",
        )

        result = pipeline._runtime.call_first(
            "approve_tool_call",
            tool_name="file_read",
            arguments={"path": "/tmp/test.txt"},
        )
        assert isinstance(result, Approve)

    @pytest.mark.asyncio
    async def test_memory_grounding_flow(self, tmp_path):
        from coding_agent.__main__ import create_agent

        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path,
            api_key="sk-test",
        )

        results = pipeline._runtime.call_many("build_context", tape=ctx.tape)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_golden_path_tool_assisted_turn(self, tmp_path):
        """Golden-path test: user msg -> model emits tool_call -> approval ->
        tool executed -> result appended -> model emits text -> tape committed.
        """
        from unittest.mock import AsyncMock
        from coding_agent.__main__ import create_agent
        from agentkit.directive.types import Approve
        from agentkit.providers.models import TextEvent, ToolCallEvent, DoneEvent
        from agentkit.tape.models import Entry

        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path,
            api_key="sk-test",
        )

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc-001",
                    name="file_read",
                    arguments={"path": "test.txt"},
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="The file contains: hello world")
                yield DoneEvent()

        mock_provider = AsyncMock()
        mock_provider.stream = mock_stream

        llm_plugin = pipeline._registry.get("llm_provider")
        llm_plugin._instance = mock_provider

        await pipeline.mount(ctx)

        ctx.tape.append(
            Entry(
                kind="message",
                payload={"role": "user", "content": "Read the file test.txt"},
            )
        )

        result = await pipeline.run_turn(ctx)

        entries = list(ctx.tape)
        kinds = [e.kind for e in entries]

        assert kinds[0] == "message"
        assert "tool_call" in kinds
        assert "tool_result" in kinds
        assert entries[-1].kind == "message"
        assert entries[-1].payload["role"] == "assistant"
        assert "hello world" in entries[-1].payload["content"]

        assert call_count == 2
