"""End-to-end pipeline integration tests — all layers wire together through real Pipeline."""

import json
import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agentkit.providers.models import DoneEvent, TextEvent, ToolCallEvent
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.tape.extract import Visibility, extract_turns
from agentkit.tape.models import Entry
from agentkit.tape.store import ForkTapeStore

from coding_agent.__main__ import create_agent
from coding_agent.adapter import PipelineAdapter
from coding_agent.adapter_types import StopReason, TurnOutcome
from coding_agent.evaluation import build_test_cases, load_tape_entries
from coding_agent.plugins.storage import JSONLTapeStore
from coding_agent.wire.protocol import (
    StreamDelta,
    ToolCallDelta,
    ToolResultDelta,
    TurnEnd,
)

CONFIG_PATH = (
    Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
)
GOLDEN_SPEC_PATH = (
    Path(__file__).parent.parent.parent
    / "data"
    / "eval"
    / "golden"
    / "parent-child-subagent-001.yaml"
)


def _skip_if_no_config():
    if not CONFIG_PATH.exists():
        pytest.skip("agent.toml not found")


def _setup_agent(tmp_path, *, approval_mode: str | None = None):
    _skip_if_no_config()
    pipeline, ctx = create_agent(
        config_path=CONFIG_PATH,
        data_dir=tmp_path,
        api_key="sk-test",
        approval_mode_override=approval_mode,
    )
    return pipeline, ctx


def _mock_provider(pipeline, stream_fn):
    mock = AsyncMock()
    mock.stream = stream_fn
    llm_plugin = pipeline._registry.get("llm_provider")
    llm_plugin._instance = mock
    return mock


def _real_provider_env_name(provider_name: str) -> str:
    if provider_name == "copilot":
        return "GITHUB_TOKEN"
    if provider_name == "kimi":
        return "MOONSHOT_API_KEY"
    if provider_name in {"kimi-code", "kimi-code-anthropic"}:
        return "KIMI_CODE_API_KEY"
    return "AGENT_API_KEY"


def _skip_if_no_deepeval() -> None:
    pytest.importorskip("deepeval")


def _skip_if_no_metric_judge_credentials() -> None:
    if os.environ.get("KIMI_CODE_API_KEY"):
        return
    if os.environ.get("MOONSHOT_API_KEY"):
        return
    if os.environ.get("OPENAI_API_KEY"):
        return
    pytest.skip(
        "ToolCorrectnessMetric tests require KIMI_CODE_API_KEY, MOONSHOT_API_KEY, or OPENAI_API_KEY"
    )


class TestPipelineE2E:
    @pytest.mark.asyncio
    async def test_subagent_tool_executes_from_real_pipeline_turn(self, tmp_path):
        pipeline, ctx = _setup_agent(tmp_path, approval_mode="yolo")
        ctx.config["max_tool_rounds"] = 3

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            del messages, tools, kwargs
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc-subagent",
                    name="subagent",
                    arguments={"goal": "Inspect child task"},
                )
                yield DoneEvent()
            elif call_count == 2:
                yield TextEvent(text="Child finished summary")
                yield DoneEvent()
            else:
                yield TextEvent(text="Parent received child result")
                yield DoneEvent()

        _mock_provider(pipeline, mock_stream)
        await pipeline.mount(ctx)

        adapter = PipelineAdapter(pipeline, ctx, consumer=None)
        outcome = await adapter.run_turn("Use a subagent")

        assert outcome.stop_reason == StopReason.NO_TOOL_CALLS
        assert outcome.final_message == "Parent received child result"

        tool_results = ctx.tape.filter("tool_result")
        visible_tool_results = [
            entry for entry in tool_results if not entry.meta.get("skip_context")
        ]
        hidden_child_entries = [
            entry for entry in list(ctx.tape) if entry.meta.get("skip_context")
        ]
        assert len(visible_tool_results) == 1
        assert visible_tool_results[0].payload["content"] == (
            "Subagent completed: Child finished summary"
        )
        assert any(
            entry.kind == "message"
            and entry.payload.get("content") == "Inspect child task"
            for entry in hidden_child_entries
        )
        assert any(
            entry.kind == "message"
            and entry.payload.get("content") == "Child finished summary"
            for entry in hidden_child_entries
        )

    @pytest.mark.asyncio
    async def test_subagent_turn_persisted_tape_flows_into_eval_adapter(self, tmp_path):
        pipeline, ctx = _setup_agent(tmp_path, approval_mode="yolo")
        ctx.config["max_tool_rounds"] = 3

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            del messages, tools, kwargs
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc-subagent",
                    name="subagent",
                    arguments={"goal": "child task"},
                )
                yield DoneEvent()
            elif call_count == 2:
                yield TextEvent(text="child done")
                yield DoneEvent()
            else:
                yield TextEvent(text="parent done")
                yield DoneEvent()

        _mock_provider(pipeline, mock_stream)
        await pipeline.mount(ctx)

        adapter = PipelineAdapter(pipeline, ctx, consumer=None)
        outcome = await adapter.run_turn("parent task")

        assert outcome.stop_reason == StopReason.NO_TOOL_CALLS
        assert outcome.final_message == "parent done"

        tape_path = tmp_path / "generated-parent-child.jsonl"
        ctx.tape.save_jsonl(tape_path)

        loaded_entries = load_tape_entries(tape_path)
        visible_turns = extract_turns(loaded_entries, visibility=Visibility.VISIBLE)
        raw_turns = extract_turns(loaded_entries, visibility=Visibility.RAW)
        cases = build_test_cases(
            tape_path=tape_path,
            spec_path=GOLDEN_SPEC_PATH,
        )

        assert len(visible_turns) == 1
        assert visible_turns[0].user_input == "parent task"
        assert visible_turns[0].final_output == "parent done"
        assert [tool.name for tool in visible_turns[0].tool_calls] == ["subagent"]
        assert visible_turns[0].tool_calls[0].arguments == {"goal": "child task"}
        assert (
            visible_turns[0].tool_calls[0].result_content
            == "Subagent completed: child done"
        )

        assert any(
            entry.kind == "message"
            and entry.payload.get("role") == "user"
            and entry.meta.get("skip_context")
            and entry.payload.get("content") == "child task"
            for entry in loaded_entries
        )
        assert len(raw_turns) == 2
        assert [turn.user_input for turn in raw_turns] == ["parent task", "child task"]
        assert raw_turns[0].final_output is None
        # RAW mode exposes the hidden child user message as a turn boundary, so the
        # later visible parent assistant message becomes the second turn's final output.
        assert raw_turns[1].final_output == "parent done"
        raw_child_messages = [
            entry
            for entry in loaded_entries
            if entry.kind == "message"
            and entry.meta.get("skip_context")
            and entry.payload.get("content") == "child done"
        ]
        assert raw_child_messages

        assert len(cases) == 1
        assert cases[0].input == "parent task"
        assert cases[0].actual_output == "parent done"
        assert [tool.name for tool in cases[0].tools_called] == ["subagent"]
        assert [tool.name for tool in cases[0].expected_tools] == ["subagent"]
        assert cases[0].expected_tools[0].input_parameters == {"goal": "child task"}

    @pytest.mark.asyncio
    async def test_storage_backed_persisted_tape_round_trip(self, tmp_path):
        pipeline, ctx = _setup_agent(tmp_path, approval_mode="yolo")
        ctx.config["max_tool_rounds"] = 3

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            del messages, tools, kwargs
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc-subagent-storage",
                    name="subagent",
                    arguments={"goal": "child task"},
                )
                yield DoneEvent()
            elif call_count == 2:
                yield TextEvent(text="child done")
                yield DoneEvent()
            else:
                yield TextEvent(text="parent done")
                yield DoneEvent()

        _mock_provider(pipeline, mock_stream)
        await pipeline.mount(ctx)

        adapter = PipelineAdapter(pipeline, ctx, consumer=None)
        outcome = await adapter.run_turn("parent task")

        assert outcome.final_message == "parent done"

        storage = pipeline._runtime.call_first("provide_storage")
        assert isinstance(storage, ForkTapeStore)

        jsonl_store = JSONLTapeStore(tmp_path / "tapes")
        persisted = await jsonl_store.load(ctx.tape.tape_id)

        assert persisted
        persisted_entries = tuple(Entry.from_dict(item) for item in persisted)
        assert persisted_entries[0].kind == "tool_call"
        assert persisted_entries[0].payload["name"] == "subagent"
        assert any(
            entry.kind == "message"
            and entry.payload.get("role") == "user"
            and entry.payload.get("content") == "child task"
            and entry.meta.get("skip_context")
            for entry in persisted_entries
        )
        loaded_entries = (ctx.tape[0], *persisted_entries)
        visible_turns = extract_turns(loaded_entries, visibility=Visibility.VISIBLE)

        assert len(visible_turns) == 1
        assert visible_turns[0].user_input == "parent task"
        assert visible_turns[0].final_output == "parent done"
        assert [tool.name for tool in visible_turns[0].tool_calls] == ["subagent"]

    @pytest.mark.asyncio
    async def test_real_provider_e2e_turn_skips_without_credentials(
        self, tmp_path, monkeypatch
    ):
        provider_name = os.environ.get("AGENT_PROVIDER", "openai")
        env_name = _real_provider_env_name(provider_name)
        credential = os.environ.get(env_name)
        if not credential:
            pytest.skip(f"Real provider test requires {env_name}")

        config_path = CONFIG_PATH
        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path,
            api_key=credential,
            provider_override=provider_name,
            approval_mode_override="yolo",
        )
        monkeypatch.delenv("AGENT_API_KEY", raising=False)

        await pipeline.mount(ctx)

        adapter = PipelineAdapter(pipeline, ctx, consumer=None)
        outcome = await adapter.run_turn(
            "Reply with exactly REAL_PROVIDER_OK and do not call any tools."
        )

        assert outcome.stop_reason == StopReason.NO_TOOL_CALLS
        assert outcome.final_message is not None
        assert "REAL_PROVIDER_OK" in outcome.final_message

        tape_path = tmp_path / "real-provider-turn.jsonl"
        ctx.tape.save_jsonl(tape_path)

        loaded_entries = load_tape_entries(tape_path)
        visible_turns = extract_turns(loaded_entries, visibility=Visibility.VISIBLE)

        assert len(visible_turns) == 1
        assert visible_turns[0].user_input == (
            "Reply with exactly REAL_PROVIDER_OK and do not call any tools."
        )
        assert visible_turns[0].final_output is not None
        assert "REAL_PROVIDER_OK" in visible_turns[0].final_output
        assert visible_turns[0].tool_calls == ()

        single_turn_spec = tmp_path / "real-provider-turn.yaml"
        _ = single_turn_spec.write_text(
            'task: "Reply with exactly REAL_PROVIDER_OK and do not call any tools."\n'
            "expected_tools: []\n"
            "threshold: 1.0\n",
            encoding="utf-8",
        )
        cases = build_test_cases(
            tape_path=tape_path,
            spec_path=single_turn_spec,
        )

        assert len(cases) == 1
        assert cases[0].input == (
            "Reply with exactly REAL_PROVIDER_OK and do not call any tools."
        )
        assert "REAL_PROVIDER_OK" in cases[0].actual_output
        assert cases[0].tools_called == ()
        assert cases[0].expected_tools == ()

    @pytest.mark.asyncio
    async def test_real_provider_subagent_metric_chain(self, tmp_path, monkeypatch):
        _skip_if_no_deepeval()
        _skip_if_no_metric_judge_credentials()

        provider_name = os.environ.get("AGENT_PROVIDER", "openai")
        env_name = _real_provider_env_name(provider_name)
        credential = os.environ.get(env_name)
        if not credential:
            pytest.skip(f"Real provider test requires {env_name}")

        prompt = (
            "Call the subagent tool exactly once. "
            "Set its goal to exactly 'Reply with exactly CHILD_CHAIN_OK and do not call any tools.' "
            "After the tool returns, reply with exactly PARENT_CHAIN_OK and do not call any other tools."
        )

        pipeline, ctx = create_agent(
            config_path=CONFIG_PATH,
            data_dir=tmp_path,
            api_key=credential,
            provider_override=provider_name,
            approval_mode_override="yolo",
        )
        ctx.config["max_tool_rounds"] = 3
        ctx.config["subagent_timeout"] = 90.0
        monkeypatch.delenv("AGENT_API_KEY", raising=False)

        await pipeline.mount(ctx)

        adapter = PipelineAdapter(pipeline, ctx, consumer=None)
        outcome = await adapter.run_turn(prompt)

        assert outcome.stop_reason == StopReason.NO_TOOL_CALLS
        assert outcome.final_message is not None
        assert "PARENT_CHAIN_OK" in outcome.final_message

        tape_path = tmp_path / "real-provider-subagent-metric.jsonl"
        ctx.tape.save_jsonl(tape_path)

        loaded_entries = load_tape_entries(tape_path)
        visible_turns = extract_turns(loaded_entries, visibility=Visibility.VISIBLE)
        raw_turns = extract_turns(loaded_entries, visibility=Visibility.RAW)

        assert len(visible_turns) == 1
        assert visible_turns[0].user_input == prompt
        assert visible_turns[0].final_output is not None
        assert "PARENT_CHAIN_OK" in visible_turns[0].final_output
        assert [tool.name for tool in visible_turns[0].tool_calls] == ["subagent"]

        child_goal = visible_turns[0].tool_calls[0].arguments.get("goal")
        assert isinstance(child_goal, str)
        assert "CHILD_CHAIN_OK" in child_goal
        assert "do not call any tools" in child_goal

        result_content = visible_turns[0].tool_calls[0].result_content
        assert result_content is not None
        assert "Subagent completed:" in result_content

        assert len(raw_turns) == 2
        assert raw_turns[0].user_input == prompt
        assert raw_turns[1].user_input == child_goal

        assert any(
            entry.kind == "message"
            and entry.payload.get("role") == "user"
            and entry.meta.get("skip_context")
            and entry.payload.get("content") == child_goal
            for entry in loaded_entries
        )
        child_assistant_messages = [
            entry
            for entry in loaded_entries
            if entry.kind == "message"
            and entry.payload.get("role") == "assistant"
            and entry.meta.get("skip_context")
            and isinstance(entry.payload.get("content"), str)
        ]
        assert len(child_assistant_messages) == 1
        assert any(
            child_assistant_messages[0].payload["content"] in result_content
            for _ in [0]
        )

        single_turn_spec = tmp_path / "real-provider-subagent-metric.yaml"
        _ = single_turn_spec.write_text(
            f'task: "{prompt}"\n'
            "expected_tools:\n"
            '  - name: "subagent"\n'
            "threshold: 1.0\n",
            encoding="utf-8",
        )
        cases = build_test_cases(
            tape_path=tape_path,
            spec_path=single_turn_spec,
        )

        assert len(cases) == 1
        assert cases[0].input == prompt
        assert "PARENT_CHAIN_OK" in cases[0].actual_output
        assert [tool.name for tool in cases[0].tools_called] == ["subagent"]
        assert [tool.name for tool in cases[0].expected_tools] == ["subagent"]

        from coding_agent.evaluation.metrics import (
            make_tool_correctness_metric,
            metric_measure,
        )

        metric = make_tool_correctness_metric()
        score = await metric_measure(metric, cases[0])

        assert 0.0 <= score <= 1.0

    @pytest.mark.asyncio
    async def test_tool_correctness_metric_accepts_built_test_case(self, tmp_path):
        _skip_if_no_deepeval()
        _skip_if_no_metric_judge_credentials()

        pipeline, ctx = _setup_agent(tmp_path, approval_mode="yolo")
        ctx.config["max_tool_rounds"] = 3

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            del messages, tools, kwargs
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc-subagent-metric",
                    name="subagent",
                    arguments={"goal": "child task"},
                )
                yield DoneEvent()
            elif call_count == 2:
                yield TextEvent(text="child done")
                yield DoneEvent()
            else:
                yield TextEvent(text="parent done")
                yield DoneEvent()

        _mock_provider(pipeline, mock_stream)
        await pipeline.mount(ctx)

        adapter = PipelineAdapter(pipeline, ctx, consumer=None)
        outcome = await adapter.run_turn("parent task")

        assert outcome.final_message == "parent done"

        tape_path = tmp_path / "metric-e2e-parent-child.jsonl"
        ctx.tape.save_jsonl(tape_path)
        cases = build_test_cases(
            tape_path=tape_path,
            spec_path=GOLDEN_SPEC_PATH,
        )

        assert len(cases) == 1

        from coding_agent.evaluation.metrics import (
            make_tool_correctness_metric,
            metric_measure,
        )

        metric = make_tool_correctness_metric()
        score = await metric_measure(metric, cases[0])

        assert score >= 0.0

    @pytest.mark.asyncio
    async def test_run_command_streaming_events(self, tmp_path):
        """Given mock LLM emitting text+tool+done, when run via adapter with consumer, then StreamDelta/ToolCallDelta/TurnEnd emitted."""
        pipeline, ctx = _setup_agent(tmp_path)

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield TextEvent(text="Thinking...")
                yield ToolCallEvent(
                    tool_call_id="tc-001",
                    name="file_read",
                    arguments={"path": "readme.txt"},
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="Here is the answer.")
                yield DoneEvent()

        _mock_provider(pipeline, mock_stream)
        await pipeline.mount(ctx)

        consumer = AsyncMock()
        consumer.emit = AsyncMock()
        adapter = PipelineAdapter(pipeline, ctx, consumer=consumer)

        outcome = await adapter.run_turn("read the readme")

        assert isinstance(outcome, TurnOutcome)
        assert outcome.final_message is not None
        assert "answer" in outcome.final_message

        emitted = [call.args[0] for call in consumer.emit.call_args_list]
        emitted_types = [type(msg) for msg in emitted]

        assert StreamDelta in emitted_types
        assert ToolCallDelta in emitted_types
        assert TurnEnd in emitted_types

        deltas = [m for m in emitted if isinstance(m, StreamDelta)]
        assert any("Thinking" in d.content for d in deltas)
        assert any("answer" in d.content for d in deltas)

        tool_deltas = [m for m in emitted if isinstance(m, ToolCallDelta)]
        assert len(tool_deltas) >= 1
        assert tool_deltas[0].tool_name == "file_read"
        assert tool_deltas[0].call_id == "tc-001"

        assert isinstance(emitted[-1], TurnEnd)

    @pytest.mark.asyncio
    async def test_repl_two_turn_context_preserved(self, tmp_path):
        """Given 2-turn conversation via PipelineAdapter, when second turn runs, then tape has both exchanges and LLM sees first turn's context."""
        pipeline, ctx = _setup_agent(tmp_path)

        turn_messages_seen: list[list[dict[str, object]]] = []

        async def mock_stream(messages, tools=None, **kwargs):
            turn_messages_seen.append(list(messages))
            yield TextEvent(text=f"Reply to turn {len(turn_messages_seen)}")
            yield DoneEvent()

        _mock_provider(pipeline, mock_stream)
        await pipeline.mount(ctx)

        adapter = PipelineAdapter(pipeline, ctx, consumer=None)

        outcome1 = await adapter.run_turn("Hello, what is Python?")
        assert outcome1.stop_reason == StopReason.NO_TOOL_CALLS
        assert outcome1.final_message is not None
        assert "turn 1" in outcome1.final_message

        outcome2 = await adapter.run_turn("Tell me more about decorators")
        assert outcome2.stop_reason == StopReason.NO_TOOL_CALLS
        assert outcome2.final_message is not None
        assert "turn 2" in outcome2.final_message

        all_entries = list(ctx.tape)
        user_entries = [
            e
            for e in all_entries
            if e.kind == "message" and e.payload.get("role") == "user"
        ]
        assistant_entries = [
            e
            for e in all_entries
            if e.kind == "message" and e.payload.get("role") == "assistant"
        ]
        assert len(user_entries) == 2
        assert len(assistant_entries) == 2

        assert len(turn_messages_seen) == 2
        second_call_messages = turn_messages_seen[1]
        user_contents = [
            str(m.get("content", ""))
            for m in second_call_messages
            if m.get("role") == "user"
        ]
        assert any("Python" in c for c in user_contents), (
            "Second turn should see first turn's user message in context"
        )

    @pytest.mark.asyncio
    async def test_headless_mode_stdout(self, tmp_path, capsys):
        """Given HeadlessConsumer + mock LLM, when adapter runs, then streamed text appears on stdout."""
        from coding_agent.ui.headless import HeadlessConsumer

        pipeline, ctx = _setup_agent(tmp_path)

        async def mock_stream(messages, tools=None, **kwargs):
            yield TextEvent(text="Hello from headless mode!")
            yield DoneEvent()

        _mock_provider(pipeline, mock_stream)
        await pipeline.mount(ctx)

        consumer = HeadlessConsumer(auto_approve=True)
        adapter = PipelineAdapter(pipeline, ctx, consumer=consumer)

        outcome = await adapter.run_turn("Say hello")

        assert outcome.stop_reason == StopReason.NO_TOOL_CALLS
        assert outcome.final_message == "Hello from headless mode!"

        captured = capsys.readouterr()
        assert "Hello from headless mode!" in captured.out

    @pytest.mark.asyncio
    async def test_doom_detection_triggers(self, tmp_path):
        """Given DoomDetectorPlugin(threshold=3) + LLM emitting same tool call repeatedly, when run completes, then stop_reason is DOOM_LOOP."""
        from coding_agent.plugins.doom_detector import DoomDetectorPlugin

        pipeline, ctx = _setup_agent(tmp_path)

        doom = DoomDetectorPlugin(threshold=3)
        pipeline._registry._plugins[doom.state_key] = doom
        for hook_name, hook_fn in doom.hooks().items():
            pipeline._registry._hook_index[hook_name] = [
                fn
                for fn in pipeline._registry._hook_index.get(hook_name, [])
                if getattr(fn, "__self__", None).__class__ is not DoomDetectorPlugin
            ]
            pipeline._registry._hook_index[hook_name].append(hook_fn)
        pipeline._runtime = HookRuntime(pipeline._registry)

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            yield ToolCallEvent(
                tool_call_id=f"tc-{call_count:03d}",
                name="file_read",
                arguments={"path": "stuck.txt"},
            )
            yield DoneEvent()

        _mock_provider(pipeline, mock_stream)
        await pipeline.mount(ctx)

        adapter = PipelineAdapter(pipeline, ctx, consumer=None)
        outcome = await adapter.run_turn("do something")

        assert outcome.stop_reason == StopReason.DOOM_LOOP
        assert call_count >= 3

        tool_calls = ctx.tape.filter("tool_call")
        assert len(tool_calls) >= 3
        assert all(tc.payload["name"] == "file_read" for tc in tool_calls)

    @pytest.mark.asyncio
    async def test_tool_error_recovery(self, tmp_path):
        """Given a tool that raises RuntimeError, when pipeline executes it, then error is recorded in tape and LLM recovers with text response."""
        pipeline, ctx = _setup_agent(tmp_path, approval_mode="yolo")

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc-err",
                    name="bash_run",
                    arguments={"command": "this_will_fail_in_tool"},
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="I encountered an error but recovered.")
                yield DoneEvent()

        _mock_provider(pipeline, mock_stream)

        original_call_first = pipeline._runtime.call_first

        def patched_call_first(hook_name, **kwargs):
            if hook_name == "execute_tool" and kwargs.get("name") == "bash_run":
                raise RuntimeError("Tool execution failed: command not found")
            return original_call_first(hook_name, **kwargs)

        pipeline._runtime.call_first = patched_call_first

        await pipeline.mount(ctx)

        adapter = PipelineAdapter(pipeline, ctx, consumer=None)
        outcome = await adapter.run_turn("run a command")

        assert isinstance(outcome, TurnOutcome)

        tool_results = ctx.tape.filter("tool_result")
        assert len(tool_results) >= 1
        error_results = [
            tr for tr in tool_results if "Error" in tr.payload.get("content", "")
        ]
        assert len(error_results) >= 1

        assert outcome.final_message is not None
        assert "recovered" in outcome.final_message

    @pytest.mark.asyncio
    async def test_large_tool_result_truncated(self, tmp_path):
        """Given a tool returning 20k chars and max_tool_result_size=10000, when pipeline processes it, then tape entry is truncated with notice."""
        pipeline, ctx = _setup_agent(tmp_path, approval_mode="yolo")

        ctx.config["max_tool_result_size"] = 10000
        large_result = "X" * 20000

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc-big",
                    name="bash_run",
                    arguments={"command": "generate_large_output"},
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="Done processing large output.")
                yield DoneEvent()

        _mock_provider(pipeline, mock_stream)

        original_call_first = pipeline._runtime.call_first

        def patched_call_first(hook_name, **kwargs):
            if hook_name == "execute_tool" and kwargs.get("name") == "bash_run":
                return large_result
            return original_call_first(hook_name, **kwargs)

        pipeline._runtime.call_first = patched_call_first

        await pipeline.mount(ctx)

        adapter = PipelineAdapter(pipeline, ctx, consumer=None)
        outcome = await adapter.run_turn("generate something large")

        assert isinstance(outcome, TurnOutcome)

        tool_results = ctx.tape.filter("tool_result")
        assert len(tool_results) >= 1

        content = tool_results[0].payload["content"]
        assert len(content) < 20000
        assert "truncated" in content.lower()
        assert "10000 chars truncated" in content

    @pytest.mark.asyncio
    async def test_parallel_tools_execute_concurrently(self, tmp_path):
        """Given ParallelExecutorPlugin + 2 independent tool calls in one round, when pipeline runs, then both execute and results appear in tape."""
        from coding_agent.plugins.parallel_executor import ParallelExecutorPlugin

        pipeline, ctx = _setup_agent(tmp_path)

        executed_tools: list[str] = []

        async def mock_execute_fn(name: str, arguments: dict[str, object]) -> str:
            executed_tools.append(name)
            await asyncio.sleep(0.01)
            return f"result_of_{name}({arguments})"

        parallel_plugin = ParallelExecutorPlugin(
            execute_fn=mock_execute_fn,
            max_concurrency=5,
        )
        pipeline._registry._plugins[parallel_plugin.state_key] = parallel_plugin
        for hook_name, hook_fn in parallel_plugin.hooks().items():
            pipeline._registry._hook_index[hook_name] = [
                fn
                for fn in pipeline._registry._hook_index.get(hook_name, [])
                if getattr(fn, "__self__", None).__class__ is not ParallelExecutorPlugin
            ]
            pipeline._registry._hook_index[hook_name].append(hook_fn)
        pipeline._runtime = HookRuntime(pipeline._registry)

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc-p1",
                    name="file_read",
                    arguments={"path": "file_a.txt"},
                )
                yield ToolCallEvent(
                    tool_call_id="tc-p2",
                    name="grep_search",
                    arguments={"pattern": "test", "path": "."},
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="Both tools completed.")
                yield DoneEvent()

        _mock_provider(pipeline, mock_stream)
        await pipeline.mount(ctx)

        adapter = PipelineAdapter(pipeline, ctx, consumer=None)
        outcome = await adapter.run_turn("read two files")

        assert isinstance(outcome, TurnOutcome)

        tool_calls = ctx.tape.filter("tool_call")
        tool_call_names = [tc.payload["name"] for tc in tool_calls]
        assert "file_read" in tool_call_names
        assert "grep_search" in tool_call_names

        tool_results = ctx.tape.filter("tool_result")
        assert len(tool_results) >= 2

    @pytest.mark.asyncio
    async def test_parallel_structured_results_preserved_in_events(self, tmp_path):
        pipeline, ctx = _setup_agent(tmp_path, approval_mode="yolo")
        ctx.config["structured_results"] = True

        core_tools = pipeline._registry.get("core_tools")
        execute_calls: list[tuple[str, dict[str, object]]] = []

        async def fake_execute_tool_async(
            name: str, arguments: dict[str, object]
        ) -> dict[str, object]:
            execute_calls.append((name, arguments))
            return {"tool": name, "arguments": arguments, "kind": "structured"}

        core_tools.execute_tool_async = fake_execute_tool_async

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc-s1",
                    name="file_read",
                    arguments={"path": "file_a.txt"},
                )
                yield ToolCallEvent(
                    tool_call_id="tc-s2",
                    name="grep_search",
                    arguments={"pattern": "test", "path": "."},
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="Both tools completed.")
                yield DoneEvent()

        _mock_provider(pipeline, mock_stream)
        await pipeline.mount(ctx)

        consumer = AsyncMock()
        consumer.emit = AsyncMock()
        adapter = PipelineAdapter(pipeline, ctx, consumer=consumer)
        outcome = await adapter.run_turn("read two files")

        tool_result_deltas = [
            call.args[0]
            for call in consumer.emit.call_args_list
            if isinstance(call.args[0], ToolResultDelta)
        ]

        assert isinstance(outcome, TurnOutcome)
        assert len(tool_result_deltas) == 2
        assert all(isinstance(delta.result, dict) for delta in tool_result_deltas)
        assert {delta.result["tool"] for delta in tool_result_deltas} == {
            "file_read",
            "grep_search",
        }

        assert execute_calls == [
            ("file_read", {"path": "file_a.txt"}),
            ("grep_search", {"pattern": "test", "path": "."}),
        ]

        tool_results = ctx.tape.filter("tool_result")
        assert [json.loads(result.payload["content"]) for result in tool_results] == [
            {
                "tool": "file_read",
                "arguments": {"path": "file_a.txt"},
                "kind": "structured",
            },
            {
                "tool": "grep_search",
                "arguments": {"pattern": "test", "path": "."},
                "kind": "structured",
            },
        ]

        assert outcome.final_message is not None
        assert "completed" in outcome.final_message
