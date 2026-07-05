import asyncio
import logging

import pytest
from unittest.mock import MagicMock
from agentkit.directive.types import Reject
from agentkit.environment import WorkspaceSummary
from agentkit.observability import SpanRecord
from agentkit.runtime import (
    AgentRunContext,
    ContextBudget,
    InMemoryRuntimeMessageBus,
    RuntimeMessage,
    RuntimeMessageCursor,
    RuntimeMessageKind,
)
from agentkit.runtime.pipeline import Pipeline, PipelineContext
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.plugin.registry import PluginRegistry
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry
from agentkit.tape.anchor import Anchor
from agentkit.errors import HookError, HookTypeError, PipelineError
from agentkit.tools import FatalToolExecutionError
from agentkit.tools import UNHANDLED_TOOL_RESULT
from agentkit.tools.schema import ToolSchema


class RecordingObservationSink:
    def __init__(self):
        self.spans: list[SpanRecord] = []

    def record_span(self, span: SpanRecord) -> None:
        self.spans.append(span)

    def record_event(self, event) -> None:
        del event


class MinimalPlugin:
    state_key = "minimal"

    def __init__(self):
        self.mounted = False
        self.mount_called = False
        self.shutdown_called = False
        self._mock_llm = MagicMock()
        self._mock_storage = object()
        self._summary_result = None

    def hooks(self):
        return {
            "mount": self.do_mount,
            "on_shutdown": self.on_shutdown,
            "provide_llm": self.provide_llm,
            "provide_storage": self.provide_storage,
            "get_tools": self.get_tools,
            "build_context": self.build_context,
            "summarize_context": self.summarize_context,
        }

    def do_mount(self, **kwargs):
        self.mount_called = True
        return {"ready": True}

    def on_shutdown(self, **kwargs):
        self.shutdown_called = True

    def provide_llm(self, **kwargs):
        return self._mock_llm

    def provide_storage(self, **kwargs):
        return self._mock_storage

    def get_tools(self, **kwargs):
        return []

    def build_context(self, **kwargs):
        return []

    def summarize_context(self, **kwargs):
        return self._summary_result


class GreedyToolPlugin:
    state_key = "greedy_tool"

    def hooks(self):
        return {
            "execute_tool": self.execute_tool,
        }

    def execute_tool(self, name: str = "", **kwargs):
        if name != "known_tool":
            return UNHANDLED_TOOL_RESULT
        return "known-tool-result"


class SkillsLikePlugin:
    state_key = "skills_like"

    def hooks(self):
        return {
            "get_tools": self.get_tools,
            "execute_tool": self.execute_tool,
        }

    def get_tools(self, **kwargs):
        return [
            ToolSchema(
                name="skill_invoke",
                description="Activate a skill",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                    },
                    "required": ["name"],
                },
            )
        ]

    def execute_tool(
        self, name: str = "", arguments: dict[str, object] | None = None, **kwargs
    ):
        if name != "skill_invoke":
            return UNHANDLED_TOOL_RESULT
        return f"activated:{(arguments or {}).get('name', '')}"


class BatchToolPlugin:
    state_key = "batch_tool"

    def __init__(self, batch_results):
        self.batch_results = batch_results

    def hooks(self):
        return {
            "execute_tools_batch": self.execute_tools_batch,
        }

    def execute_tools_batch(self, tool_calls=None, **kwargs):
        del tool_calls, kwargs
        return self.batch_results


class FatalExecuteToolPlugin:
    state_key = "fatal_execute_tool"

    def __init__(self):
        self.batch_called = False

    def hooks(self):
        return {
            "get_tools": self.get_tools,
            "execute_tools_batch": self.execute_tools_batch,
        }

    def get_tools(self, **kwargs):
        del kwargs
        return [
            ToolSchema(
                name="fatal_tool",
                description="Fatal tool for testing",
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            )
        ]

    def execute_tools_batch(self, tool_calls=None, **kwargs):
        del kwargs
        assert tool_calls is not None
        assert len(tool_calls) == 2
        assert all(call.get("name") == "fatal_tool" for call in tool_calls)
        self.batch_called = True
        raise FatalToolExecutionError("fatal batch tool failure")


class SchemaValidatedToolPlugin:
    state_key = "schema_validated_tool"

    def __init__(self):
        self.execute_calls = 0

    def hooks(self):
        return {
            "get_tools": self.get_tools,
            "execute_tool": self.execute_tool,
        }

    def get_tools(self, **kwargs):
        return [
            ToolSchema(
                name="file_read",
                description="Read a file",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            )
        ]

    def execute_tool(self, **kwargs):
        del kwargs
        self.execute_calls += 1
        return "should-not-run"


class ProxyToolPlugin:
    state_key = "proxy_tool"

    def __init__(self):
        self.calls: list[tuple[str, dict[str, object]]] = []

    def hooks(self):
        return {
            "get_proxy_tools": self.get_proxy_tools,
            "execute_proxy_tool": self.execute_proxy_tool,
        }

    def get_proxy_tools(self, **kwargs):
        del kwargs
        return [
            ToolSchema(
                name="dynamic_echo",
                description="Dynamic echo tool",
                parameters={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            )
        ]

    def execute_proxy_tool(
        self, name: str = "", arguments: dict[str, object] | None = None, **kwargs
    ):
        del kwargs
        self.calls.append((name, arguments or {}))
        if name != "dynamic_echo":
            return UNHANDLED_TOOL_RESULT
        return f"proxy:{(arguments or {}).get('value')}"


class RejectingCallToolApprovalPlugin:
    state_key = "rejecting_call_tool_approval"

    def __init__(self):
        self.calls: list[tuple[str, dict[str, object]]] = []

    def hooks(self):
        return {"approve_tool_call": self.approve_tool_call}

    def approve_tool_call(
        self,
        tool_name: str = "",
        arguments: dict[str, object] | None = None,
        **kwargs,
    ):
        del kwargs
        self.calls.append((tool_name, arguments or {}))
        if tool_name == "call_tool":
            return Reject(reason="outer affordance should not be approved")
        return None


class RuntimeContextEnvironment:
    @property
    def kind(self) -> str:
        return "local"

    def tool_config(self) -> dict[str, object]:
        return {"workspace_root": "/repo"}

    def workspace_summary(self) -> WorkspaceSummary:
        return WorkspaceSummary(
            display_name="/repo",
            default_cwd="/repo",
            local_root="/repo",
        )

    def build_file_tools(self):
        raise NotImplementedError

    def build_file_patch_tool(self):
        raise NotImplementedError

    def build_shell_tool(self):
        raise NotImplementedError


class TestPipelineContext:
    def test_create_context(self):
        tape = Tape()
        ctx = PipelineContext(
            tape=tape,
            session_id="ses-1",
            config={"model": "gpt-4"},
        )
        assert ctx.tape is tape
        assert ctx.session_id == "ses-1"
        assert ctx.config["model"] == "gpt-4"
        assert ctx.plugin_states == {}

    def test_context_plugin_state_access(self):
        ctx = PipelineContext(tape=Tape(), session_id="x")
        ctx.plugin_states["memory"] = {"last_query": "test"}
        assert ctx.plugin_states["memory"]["last_query"] == "test"


class TestPipeline:
    @pytest.fixture
    def setup(self):
        registry = PluginRegistry()
        plugin = MinimalPlugin()
        registry.register(plugin)
        runtime = HookRuntime(registry)
        pipeline = Pipeline(runtime=runtime, registry=registry)
        return pipeline, plugin

    def test_pipeline_creates(self, setup):
        pipeline, _ = setup
        assert pipeline is not None

    @pytest.mark.asyncio
    async def test_mount_calls_plugins(self, setup):
        pipeline, plugin = setup
        ctx = PipelineContext(tape=Tape(), session_id="s1")
        await pipeline.mount(ctx)
        assert plugin.mount_called

    @pytest.mark.asyncio
    async def test_mount_populates_plugin_states(self, setup):
        pipeline, _ = setup
        ctx = PipelineContext(tape=Tape(), session_id="s1")
        await pipeline.mount(ctx)
        assert "minimal" in ctx.plugin_states

    @pytest.mark.asyncio
    async def test_mount_initializes_toolset_once(self, setup):
        pipeline, plugin = setup
        ctx = PipelineContext(tape=Tape(), session_id="s1")

        await pipeline.mount(ctx)
        mounted_toolset = ctx.toolset

        assert mounted_toolset is not None

        async def mock_stream(messages, tools=None, **kwargs):
            del messages, tools, kwargs
            from agentkit.providers.models import DoneEvent, TextEvent

            yield TextEvent(text="Hello back!")
            yield DoneEvent()

        plugin._mock_llm.stream = mock_stream

        await pipeline.run_turn(ctx)

        assert ctx.toolset is mounted_toolset

    @pytest.mark.asyncio
    async def test_run_turn_records_runtime_stage_spans(self, setup):
        pipeline, plugin = setup
        sink = RecordingObservationSink()

        async def mock_stream(messages, tools=None, **kwargs):
            del messages, tools, kwargs
            from agentkit.providers.models import DoneEvent, TextEvent

            yield TextEvent(text="Hello back!")
            yield DoneEvent()

        plugin._mock_llm.stream = mock_stream
        ctx = PipelineContext(
            tape=Tape(tape_id="tape-1"),
            session_id="session-1",
            run_context=AgentRunContext(
                session_id="session-1",
                run_id="run-1",
                agent_id="root",
                environment=RuntimeContextEnvironment(),
                context_budget=ContextBudget(),
                trace_metadata={
                    "turn_id": "run-1",
                    "tool_call_id": "tool-call-1",
                    "interaction_id": "interaction-1",
                    "event_id": "event-1",
                    "checkpoint_id": "checkpoint-1",
                    "prompt": "do-not-record",
                    "message": "do-not-record",
                    "result": "do-not-record",
                    "secret": "do-not-record",
                    "text": "do-not-record",
                    "ignored_object": {"nested": "do-not-record"},
                },
            ),
            config={"observation_sink": sink},
        )

        await pipeline.run_turn(ctx)

        stage_spans = [
            span for span in sink.spans if span.name.startswith("runtime.stage.")
        ]
        assert [span.name for span in stage_spans] == [
            "runtime.stage.resolve",
            "runtime.stage.load_state",
            "runtime.stage.build_context",
            "runtime.stage.model_generate",
            "runtime.stage.save_tape",
            "runtime.stage.apply_directives",
            "runtime.stage.dispatch",
        ]
        assert [span.status for span in stage_spans] == ["ok"] * 7
        build_context_span = stage_spans[2]
        assert build_context_span.attributes == {
            "stage": "build_context",
            "session_id": "session-1",
            "run_id": "run-1",
            "agent_id": "root",
            "tape_id": "tape-1",
            "turn_id": "run-1",
            "tool_call_id": "tool-call-1",
            "interaction_id": "interaction-1",
            "event_id": "event-1",
            "checkpoint_id": "checkpoint-1",
            "entry_count_before": 0,
            "entry_count_after": 0,
        }
        assert {
            "prompt",
            "message",
            "result",
            "secret",
            "text",
            "ignored_object",
        }.isdisjoint(build_context_span.attributes)
        model_span = stage_spans[3]
        assert model_span.attributes["entry_count_before"] == 0
        assert model_span.attributes["entry_count_after"] == 1

    @pytest.mark.asyncio
    async def test_run_turn_records_llm_generation_span_with_usage_metadata(
        self, setup
    ):
        pipeline, plugin = setup
        sink = RecordingObservationSink()

        async def mock_stream(messages, tools=None, **kwargs):
            del messages, tools, kwargs
            from agentkit.providers.models import DoneEvent, TextEvent, UsageEvent

            yield UsageEvent(
                input_tokens=12,
                output_tokens=5,
                provider_name="test-provider",
            )
            yield TextEvent(text="Hello back!")
            yield DoneEvent()

        plugin._mock_llm.stream = mock_stream
        ctx = PipelineContext(
            tape=Tape(tape_id="tape-1"),
            session_id="session-1",
            run_context=AgentRunContext(
                session_id="session-1",
                run_id="run-1",
                agent_id=None,
                environment=RuntimeContextEnvironment(),
                context_budget=ContextBudget(),
                trace_metadata={"turn_id": "run-1"},
            ),
            config={
                "model": "test-model",
                "observation_sink": sink,
            },
        )

        await pipeline.run_turn(ctx)

        llm_spans = [span for span in sink.spans if span.name == "llm.generation"]
        assert len(llm_spans) == 1
        assert llm_spans[0].status == "ok"
        assert llm_spans[0].attributes == {
            "session_id": "session-1",
            "run_id": "run-1",
            "tape_id": "tape-1",
            "turn_id": "run-1",
            "message_count": 2,
            "tool_schema_count": 0,
            "model": "test-model",
            "provider_name": "test-provider",
            "input_tokens": 12,
            "output_tokens": 5,
            "total_tokens": 17,
        }

    @pytest.mark.asyncio
    async def test_run_turn_records_llm_generation_error_span(self, setup):
        pipeline, plugin = setup
        sink = RecordingObservationSink()

        async def mock_stream(messages, tools=None, **kwargs):
            del messages, tools, kwargs
            raise RuntimeError("provider exploded")
            yield

        plugin._mock_llm.stream = mock_stream
        ctx = PipelineContext(
            tape=Tape(),
            session_id="session-1",
            config={"observation_sink": sink},
        )

        with pytest.raises(PipelineError, match="provider exploded"):
            await pipeline.run_turn(ctx)

        llm_spans = [span for span in sink.spans if span.name == "llm.generation"]
        assert len(llm_spans) == 1
        assert llm_spans[0].status == "error"
        assert llm_spans[0].error_type == "RuntimeError"
        assert llm_spans[0].error_message == "provider exploded"

    @pytest.mark.asyncio
    async def test_run_turn_records_error_stage_span_and_preserves_pipeline_error(
        self, setup
    ):
        pipeline, _ = setup
        sink = RecordingObservationSink()

        async def fail_build_context(ctx):
            del ctx
            raise RuntimeError("context exploded")

        pipeline._stage_build_context = fail_build_context
        ctx = PipelineContext(
            tape=Tape(),
            session_id="session-1",
            config={"observation_sink": sink},
        )

        with pytest.raises(PipelineError, match="context exploded"):
            await pipeline.run_turn(ctx)

        assert [span.name for span in sink.spans] == [
            "runtime.stage.resolve",
            "runtime.stage.load_state",
            "runtime.stage.build_context",
        ]
        error_span = sink.spans[-1]
        assert error_span.status == "error"
        assert error_span.error_type == "RuntimeError"
        assert error_span.error_message == "context exploded"
        assert error_span.attributes["stage"] == "build_context"
        assert error_span.attributes["session_id"] == "session-1"

    @pytest.mark.asyncio
    async def test_runtime_context_and_messages_are_injected_into_prompt(self, setup):
        pipeline, plugin = setup
        bus = InMemoryRuntimeMessageBus()
        await bus.publish(
            RuntimeMessage(
                message_id="msg-steer",
                kind=RuntimeMessageKind.USER_STEER,
                payload={"text": "Prefer a short answer"},
            )
        )
        await bus.publish(
            RuntimeMessage(
                message_id="msg-notice",
                kind=RuntimeMessageKind.SYSTEM_NOTICE,
                payload={"text": "Checkpoint restored"},
            )
        )

        captured_messages: list[list[dict[str, object]]] = []

        async def mock_stream(messages, tools=None, **kwargs):
            del tools, kwargs
            captured_messages.append(messages)
            from agentkit.providers.models import DoneEvent, TextEvent

            yield TextEvent(text="Hello back!")
            yield DoneEvent()

        plugin._mock_llm.stream = mock_stream

        ctx = PipelineContext(
            tape=Tape(),
            session_id="session-1",
            run_context=AgentRunContext(
                session_id="session-1",
                run_id="run-1",
                agent_id="agent-main",
                parent_run_id="parent-run",
                environment=RuntimeContextEnvironment(),
                context_budget=ContextBudget(
                    max_input_tokens=128000,
                    reserved_output_tokens=4096,
                    max_output_tokens=8192,
                ),
            ),
            runtime_message_bus=bus,
            active_approvals=[
                {"request_id": "req-1", "tool": "bash_run"},
            ],
            config={"system_prompt": "system"},
        )

        await pipeline.run_turn(ctx)

        system_content = "\n".join(
            str(message.get("content", ""))
            for message in captured_messages[0]
            if message.get("role") == "system"
        )
        assert "Runtime context" in system_content
        assert "session_id: session-1" in system_content
        assert "run_id: run-1" in system_content
        assert "agent_id: agent-main" in system_content
        assert "parent_run_id: parent-run" in system_content
        assert "environment: local" in system_content
        assert "workspace_root: /repo" in system_content
        assert "elapsed_seconds:" in system_content
        assert (
            "context_budget: max_input=128000 reserved_output=4096 max_output=8192"
            in system_content
        )
        assert "active_approvals: req-1:bash_run" in system_content
        assert "user_steer msg-steer: Prefer a short answer" in system_content
        assert "system_notice msg-notice: Checkpoint restored" in system_content
        assert ctx.runtime_message_cursor.sequence == 2

    @pytest.mark.asyncio
    async def test_runtime_message_consumed_after_build_context_reaches_prompt(
        self, setup
    ):
        pipeline, plugin = setup
        bus = InMemoryRuntimeMessageBus()
        captured_messages: list[list[dict[str, object]]] = []

        async def mock_stream(messages, tools=None, **kwargs):
            del tools, kwargs
            captured_messages.append(messages)
            from agentkit.providers.models import DoneEvent, TextEvent

            yield TextEvent(text="Hello back!")
            yield DoneEvent()

        original_build_context = pipeline._stage_build_context
        published_late_message = False

        async def build_context_then_publish(ctx):
            nonlocal published_late_message
            await original_build_context(ctx)
            if published_late_message:
                return
            published_late_message = True
            await bus.publish(
                RuntimeMessage(
                    message_id="msg-late-steer",
                    kind=RuntimeMessageKind.USER_STEER,
                    payload={"text": "Use the new instruction"},
                )
            )

        plugin._mock_llm.stream = mock_stream
        pipeline._stage_build_context = build_context_then_publish
        ctx = PipelineContext(
            tape=Tape(),
            session_id="session-1",
            runtime_message_bus=bus,
            config={"system_prompt": "system"},
        )

        await pipeline.run_turn(ctx)

        system_content = "\n".join(
            str(message.get("content", ""))
            for message in captured_messages[0]
            if message.get("role") == "system"
        )
        assert "user_steer msg-late-steer: Use the new instruction" in system_content
        assert ctx.runtime_message_cursor.sequence == 1

    @pytest.mark.asyncio
    async def test_prompt_runtime_message_after_run_model_is_kept_for_next_turn(
        self, setup
    ):
        pipeline, plugin = setup
        bus = InMemoryRuntimeMessageBus()
        captured_messages: list[list[dict[str, object]]] = []

        async def mock_stream(messages, tools=None, **kwargs):
            del tools, kwargs
            captured_messages.append(messages)
            from agentkit.providers.models import DoneEvent, TextEvent

            yield TextEvent(text="Hello back!")
            yield DoneEvent()

        original_run_model = pipeline._stage_run_model
        published_late_message = False

        async def run_model_then_publish(ctx):
            nonlocal published_late_message
            await original_run_model(ctx)
            if published_late_message:
                return
            published_late_message = True
            await bus.publish(
                RuntimeMessage(
                    message_id="msg-after-model",
                    kind=RuntimeMessageKind.USER_STEER,
                    payload={"text": "Use this next turn"},
                )
            )

        plugin._mock_llm.stream = mock_stream
        pipeline._stage_run_model = run_model_then_publish
        ctx = PipelineContext(
            tape=Tape(),
            session_id="session-1",
            runtime_message_bus=bus,
            config={"system_prompt": "system"},
        )

        await pipeline.run_turn(ctx)
        await pipeline.run_turn(ctx)

        first_system_content = "\n".join(
            str(message.get("content", ""))
            for message in captured_messages[0]
            if message.get("role") == "system"
        )
        second_system_content = "\n".join(
            str(message.get("content", ""))
            for message in captured_messages[1]
            if message.get("role") == "system"
        )
        assert (
            "user_steer msg-after-model: Use this next turn" not in first_system_content
        )
        assert "user_steer msg-after-model: Use this next turn" in second_system_content
        assert ctx.runtime_message_cursor.sequence == 1

    @pytest.mark.asyncio
    async def test_prompt_runtime_message_published_during_stream_is_kept_for_next_turn(
        self, setup
    ):
        pipeline, plugin = setup
        bus = InMemoryRuntimeMessageBus()
        captured_messages: list[list[dict[str, object]]] = []
        published_during_stream = False

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal published_during_stream
            del tools, kwargs
            captured_messages.append(messages)
            from agentkit.providers.models import DoneEvent, TextEvent

            if not published_during_stream:
                published_during_stream = True
                yield TextEvent(text="Hello")
                await bus.publish(
                    RuntimeMessage(
                        message_id="msg-during-stream",
                        kind=RuntimeMessageKind.USER_STEER,
                        payload={"text": "Use this after stream"},
                    )
                )
                yield TextEvent(text=" back!")
            else:
                yield TextEvent(text="Hello back!")
            yield DoneEvent()

        plugin._mock_llm.stream = mock_stream
        ctx = PipelineContext(
            tape=Tape(),
            session_id="session-1",
            runtime_message_bus=bus,
            config={"system_prompt": "system"},
        )

        await pipeline.run_turn(ctx)
        await pipeline.run_turn(ctx)

        first_system_content = "\n".join(
            str(message.get("content", ""))
            for message in captured_messages[0]
            if message.get("role") == "system"
        )
        second_system_content = "\n".join(
            str(message.get("content", ""))
            for message in captured_messages[1]
            if message.get("role") == "system"
        )
        assert (
            "user_steer msg-during-stream: Use this after stream"
            not in first_system_content
        )
        assert (
            "user_steer msg-during-stream: Use this after stream"
            in second_system_content
        )
        assert ctx.runtime_message_cursor.sequence == 1

    @pytest.mark.asyncio
    async def test_runtime_context_includes_elapsed_without_run_context(self, setup):
        pipeline, plugin = setup
        captured_messages: list[list[dict[str, object]]] = []

        async def mock_stream(messages, tools=None, **kwargs):
            del tools, kwargs
            captured_messages.append(messages)
            from agentkit.providers.models import DoneEvent, TextEvent

            yield TextEvent(text="Hello back!")
            yield DoneEvent()

        plugin._mock_llm.stream = mock_stream
        ctx = PipelineContext(
            tape=Tape(),
            session_id="session-1",
            config={"system_prompt": "system"},
        )

        await pipeline.run_turn(ctx)

        system_content = "\n".join(
            str(message.get("content", ""))
            for message in captured_messages[0]
            if message.get("role") == "system"
        )
        assert "Runtime context" in system_content
        assert "elapsed_seconds:" in system_content

    @pytest.mark.asyncio
    async def test_runtime_approval_decision_is_left_for_product_consumer(self, setup):
        pipeline, plugin = setup
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
                payload={"text": "Prefer a short answer"},
            )
        )

        captured_messages: list[list[dict[str, object]]] = []

        async def mock_stream(messages, tools=None, **kwargs):
            del tools, kwargs
            captured_messages.append(messages)
            from agentkit.providers.models import DoneEvent, TextEvent

            yield TextEvent(text="Hello back!")
            yield DoneEvent()

        plugin._mock_llm.stream = mock_stream
        ctx = PipelineContext(
            tape=Tape(),
            session_id="session-1",
            runtime_message_bus=bus,
            config={"system_prompt": "system"},
        )

        await pipeline.run_turn(ctx)

        system_content = "\n".join(
            str(message.get("content", ""))
            for message in captured_messages[0]
            if message.get("role") == "system"
        )
        approval_batch = await bus.consume_after(
            RuntimeMessageCursor(),
            kinds={RuntimeMessageKind.APPROVAL_DECISION},
        )

        assert [item.message.message_id for item in ctx.runtime_messages] == [
            "msg-steer"
        ]
        assert "user_steer msg-steer: Prefer a short answer" in system_content
        assert "approval_decision" not in system_content
        assert [item.message.message_id for item in approval_batch.messages] == [
            "msg-approval"
        ]

    @pytest.mark.asyncio
    async def test_runtime_interrupt_stops_before_model_stream(self, setup):
        pipeline, plugin = setup
        bus = InMemoryRuntimeMessageBus()
        await bus.publish(
            RuntimeMessage(
                message_id="msg-stop",
                kind=RuntimeMessageKind.INTERRUPT,
                payload={"reason": "user stopped turn"},
            )
        )
        stream_called = False

        async def mock_stream(messages, tools=None, **kwargs):
            del messages, tools, kwargs
            nonlocal stream_called
            stream_called = True
            from agentkit.providers.models import DoneEvent, TextEvent

            yield TextEvent(text="should not stream")
            yield DoneEvent()

        plugin._mock_llm.stream = mock_stream
        ctx = PipelineContext(
            tape=Tape(),
            session_id="session-1",
            runtime_message_bus=bus,
        )

        with pytest.raises(
            PipelineError, match="runtime interrupted: user stopped turn"
        ):
            await pipeline.run_turn(ctx)

        assert stream_called is False
        assert ctx.runtime_messages == []
        assert ctx.runtime_message_cursor.sequence == 0

    @pytest.mark.asyncio
    async def test_runtime_interrupt_mixed_batch_does_not_advance_cursor(self, setup):
        pipeline, plugin = setup
        bus = InMemoryRuntimeMessageBus()
        await bus.publish(
            RuntimeMessage(
                message_id="msg-steer",
                kind=RuntimeMessageKind.USER_STEER,
                payload={"text": "Keep this for retry"},
            )
        )
        await bus.publish(
            RuntimeMessage(
                message_id="msg-stop",
                kind=RuntimeMessageKind.INTERRUPT,
                payload={"reason": "user stopped turn"},
            )
        )
        stream_called = False

        async def mock_stream(messages, tools=None, **kwargs):
            del messages, tools, kwargs
            nonlocal stream_called
            stream_called = True
            from agentkit.providers.models import DoneEvent, TextEvent

            yield TextEvent(text="should not stream")
            yield DoneEvent()

        plugin._mock_llm.stream = mock_stream
        ctx = PipelineContext(
            tape=Tape(),
            session_id="session-1",
            runtime_message_bus=bus,
        )

        with pytest.raises(
            PipelineError, match="runtime interrupted: user stopped turn"
        ):
            await pipeline.run_turn(ctx)

        retry_batch = await bus.consume_after(RuntimeMessageCursor())

        assert stream_called is False
        assert ctx.runtime_messages == []
        assert ctx.runtime_message_cursor.sequence == 0
        assert [item.message.message_id for item in retry_batch.messages] == [
            "msg-steer",
            "msg-stop",
        ]

    @pytest.mark.asyncio
    async def test_shutdown_notifies_plugins(self, setup):
        pipeline, plugin = setup
        ctx = PipelineContext(tape=Tape(), session_id="s1")

        await pipeline.shutdown(ctx)

        assert plugin.shutdown_called is True

    def test_pipeline_stages_defined(self, setup):
        pipeline, _ = setup
        stages = pipeline.stage_names
        expected = [
            "resolve_session",
            "load_state",
            "build_context",
            "run_model",
            "save_state",
            "render",
            "dispatch",
        ]
        assert stages == expected

    @pytest.mark.asyncio
    async def test_run_single_turn(self, setup):
        pipeline, plugin = setup
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hello"}))
        ctx = PipelineContext(tape=tape, session_id="s1")
        await pipeline.mount(ctx)

        from agentkit.providers.models import TextEvent, DoneEvent

        async def mock_stream(messages, tools=None, **kwargs):
            yield TextEvent(text="Hello back!")
            yield DoneEvent()

        mock_llm = MagicMock()
        mock_llm.stream = mock_stream
        plugin._mock_llm = mock_llm

        result = await pipeline.run_turn(ctx)
        assert result is not None
        last_entry = list(ctx.tape)[-1]
        assert last_entry.payload["role"] == "assistant"
        assert "Hello back!" in last_entry.payload["content"]

    @pytest.mark.asyncio
    async def test_run_turn_with_tool_call(self, setup):
        pipeline, plugin = setup
        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "read file.txt"})
        )
        ctx = PipelineContext(tape=tape, session_id="s1")
        await pipeline.mount(ctx)

        from agentkit.providers.models import TextEvent, ToolCallEvent, DoneEvent

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc1", name="file_read", arguments={"path": "file.txt"}
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="File contents: test data")
                yield DoneEvent()

        mock_llm = MagicMock()
        mock_llm.stream = mock_stream
        plugin._mock_llm = mock_llm

        await pipeline.run_turn(ctx)
        entries = list(ctx.tape)
        assert any(e.kind == "tool_call" for e in entries)
        assert any(e.kind == "tool_result" for e in entries)
        assert entries[-1].payload["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_prompt_runtime_message_before_tool_execution_survives_failed_turn(
        self, setup
    ):
        pipeline, plugin = setup
        bus = InMemoryRuntimeMessageBus()
        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "read file.txt"})
        )
        ctx = PipelineContext(tape=tape, session_id="s1", runtime_message_bus=bus)
        await pipeline.mount(ctx)

        from agentkit.providers.models import DoneEvent, TextEvent, ToolCallEvent

        call_count = 0
        captured_messages: list[list[dict[str, object]]] = []
        published_late_message = False
        original_build_context = pipeline._stage_build_context
        fail_rebuild_once = False

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count, published_late_message
            del tools, kwargs
            captured_messages.append(messages)
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc1", name="file_read", arguments={"path": "file.txt"}
                )
                if not published_late_message:
                    published_late_message = True
                    await bus.publish(
                        RuntimeMessage(
                            message_id="msg-before-tool-execution",
                            kind=RuntimeMessageKind.USER_STEER,
                            payload={"text": "Keep this steer after failure"},
                        )
                    )
                yield DoneEvent()
            else:
                yield TextEvent(text="File contents: test data")
                yield DoneEvent()

        async def build_context_fail_after_tool_call(current_ctx):
            nonlocal fail_rebuild_once
            if fail_rebuild_once and any(
                entry.kind == "tool_call" for entry in current_ctx.tape
            ):
                fail_rebuild_once = False
                raise RuntimeError("rebuild exploded")
            await original_build_context(current_ctx)

        mock_llm = MagicMock()
        mock_llm.stream = mock_stream
        plugin._mock_llm = mock_llm
        pipeline._stage_build_context = build_context_fail_after_tool_call
        fail_rebuild_once = True

        with pytest.raises(PipelineError, match="rebuild exploded"):
            await pipeline.run_turn(ctx)

        assert ctx.runtime_message_cursor.sequence == 0

        await pipeline.run_turn(ctx)

        second_system_content = "\n".join(
            str(message.get("content", ""))
            for message in captured_messages[-1]
            if message.get("role") == "system"
        )
        assert (
            "user_steer msg-before-tool-execution: Keep this steer after failure"
            in second_system_content
        )
        assert ctx.runtime_message_cursor.sequence == 1

    @pytest.mark.asyncio
    async def test_run_turn_allows_later_plugin_to_handle_unknown_tool(self):
        registry = PluginRegistry()
        llm_plugin = MinimalPlugin()
        greedy_tool = GreedyToolPlugin()
        skills = SkillsLikePlugin()
        registry.register(llm_plugin)
        registry.register(greedy_tool)
        registry.register(skills)
        runtime = HookRuntime(registry)
        pipeline = Pipeline(runtime=runtime, registry=registry)

        async def mock_stream(messages, tools=None, **kwargs):
            from agentkit.providers.models import DoneEvent, TextEvent, ToolCallEvent

            if not any(entry.kind == "tool_result" for entry in ctx.tape):
                assert tools is not None
                yield ToolCallEvent(
                    tool_call_id="tc-skill-1",
                    name="skill_invoke",
                    arguments={"name": "using-superpowers"},
                )
                yield DoneEvent()
                return

            yield TextEvent(text="Skill activated")
            yield DoneEvent()

        mock_llm = MagicMock()
        mock_llm.stream = mock_stream
        llm_plugin._mock_llm = mock_llm

        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hello"}))
        ctx = PipelineContext(tape=tape, session_id="s-skill")
        await pipeline.mount(ctx)

        result = await pipeline.run_turn(ctx)

        tool_result_entries = [e for e in ctx.tape if e.kind == "tool_result"]
        assert tool_result_entries
        assert (
            tool_result_entries[0].payload["content"] == "activated:using-superpowers"
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_run_turn_rejects_tool_call_with_invalid_schema_arguments(self):
        registry = PluginRegistry()
        llm_plugin = MinimalPlugin()
        tool_plugin = SchemaValidatedToolPlugin()
        registry.register(llm_plugin)
        registry.register(tool_plugin)
        runtime = HookRuntime(registry)
        pipeline = Pipeline(runtime=runtime, registry=registry)

        async def mock_stream(messages, tools=None, **kwargs):
            from agentkit.providers.models import DoneEvent, TextEvent, ToolCallEvent

            if not any(entry.kind == "tool_result" for entry in ctx.tape):
                assert tools is not None
                yield ToolCallEvent(
                    tool_call_id="tc-invalid",
                    name="file_read",
                    arguments={"path": "a.txt", "extra": True},
                )
                yield DoneEvent()
                return

            yield TextEvent(text="Validation handled")
            yield DoneEvent()

        mock_llm = MagicMock()
        mock_llm.stream = mock_stream
        llm_plugin._mock_llm = mock_llm

        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "read"}))
        ctx = PipelineContext(tape=tape, session_id="s-validation")
        await pipeline.mount(ctx)

        await pipeline.run_turn(ctx)

        tool_result_entries = [
            entry for entry in ctx.tape if entry.kind == "tool_result"
        ]
        assert tool_result_entries[0].payload == {
            "tool_call_id": "tc-invalid",
            "content": "Tool call validation failed: unexpected argument: extra",
        }
        assert tool_plugin.execute_calls == 0

    @pytest.mark.asyncio
    async def test_run_turn_skips_outer_approval_for_call_tool_affordance(self):
        registry = PluginRegistry()
        llm_plugin = MinimalPlugin()
        proxy_plugin = ProxyToolPlugin()
        approval_plugin = RejectingCallToolApprovalPlugin()
        registry.register(llm_plugin)
        registry.register(proxy_plugin)
        registry.register(approval_plugin)
        runtime = HookRuntime(registry)
        pipeline = Pipeline(runtime=runtime, registry=registry)

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            del messages, kwargs
            nonlocal call_count
            call_count += 1
            from agentkit.providers.models import DoneEvent, TextEvent, ToolCallEvent

            if call_count == 1:
                assert tools is not None
                yield ToolCallEvent(
                    tool_call_id="tc-call-tool",
                    name="call_tool",
                    arguments={
                        "name": "dynamic_echo",
                        "arguments": {"value": "hello"},
                    },
                )
                yield DoneEvent()
                return

            yield TextEvent(text="Proxy handled")
            yield DoneEvent()

        llm_plugin._mock_llm.stream = mock_stream
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "call"}))
        ctx = PipelineContext(tape=tape, session_id="s-proxy")
        await pipeline.mount(ctx)

        await pipeline.run_turn(ctx)

        tool_results = [entry for entry in ctx.tape if entry.kind == "tool_result"]
        assert tool_results[0].payload == {
            "tool_call_id": "tc-call-tool",
            "content": "proxy:hello",
        }
        assert approval_plugin.calls == [("dynamic_echo", {"value": "hello"})]
        assert proxy_plugin.calls == [("dynamic_echo", {"value": "hello"})]

    @pytest.mark.asyncio
    async def test_run_turn_commits_active_fork_and_updates_context_tape(self, setup):
        pipeline, plugin = setup

        class RecordingStorage:
            def __init__(self):
                self.begin_calls = []
                self.commit_calls = []
                self.rollback_calls = []
                self.stable_tape_id = "stable-tape-id"

            def begin(self, tape):
                self.begin_calls.append(tape)
                return tape.fork()

            async def commit(self, tape):
                self.commit_calls.append(tape)
                return self.stable_tape_id

            def rollback(self, tape):
                self.rollback_calls.append(tape)

        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hello"}))
        ctx = PipelineContext(tape=tape, session_id="s1")
        await pipeline.mount(ctx)

        storage = RecordingStorage()
        plugin._mock_storage = storage

        from agentkit.providers.models import TextEvent, DoneEvent

        async def mock_stream(messages, tools=None, **kwargs):
            yield TextEvent(text="Hello back!")
            yield DoneEvent()

        mock_llm = MagicMock()
        mock_llm.stream = mock_stream
        plugin._mock_llm = mock_llm

        original_tape = ctx.tape
        await pipeline.run_turn(ctx)

        assert storage.begin_calls == [original_tape]
        assert len(storage.commit_calls) == 1
        assert storage.rollback_calls == []
        assert ctx.tape is storage.commit_calls[0]
        assert ctx.tape is not original_tape
        assert ctx.tape.parent_id == original_tape.tape_id
        assert ctx.tape.tape_id == storage.stable_tape_id

    @pytest.mark.asyncio
    async def test_build_context_applies_summary_entries(self, setup):
        pipeline, plugin = setup
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "older"}))
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "latest"})
        )
        ctx = PipelineContext(tape=tape, session_id="s1")

        summary_entries = [
            Entry(kind="anchor", payload={"content": "summary"}),
            Entry(kind="message", payload={"role": "user", "content": "latest"}),
        ]
        plugin._summary_result = summary_entries

        await pipeline._stage_build_context(ctx)

        entries = list(ctx.tape)
        assert len(entries) == 2
        assert entries[0].kind == "anchor"
        assert entries[0].payload["content"] == "summary"
        assert ctx.messages[1]["role"] == "system"
        assert ctx.messages[1]["content"] == "summary"

    @pytest.mark.asyncio
    async def test_build_context_awaits_async_hook_grounding(self, setup):
        _pipeline, plugin = setup

        class AsyncContextPlugin:
            state_key = "async_context"

            def __init__(self):
                self.awaited = False

            def hooks(self):
                return {"build_context": self.build_context}

            async def build_context(self, **kwargs):
                del kwargs
                self.awaited = True
                return [{"role": "system", "content": "async grounding"}]

        async_plugin = AsyncContextPlugin()
        registry = PluginRegistry()
        registry.register(plugin)
        registry.register(async_plugin)
        pipeline = Pipeline(runtime=HookRuntime(registry), registry=registry)
        ctx = PipelineContext(tape=Tape(), session_id="s1")

        await pipeline._stage_build_context(ctx)

        assert async_plugin.awaited is True
        assert {"role": "system", "content": "async grounding"} in ctx.messages

    @pytest.mark.asyncio
    async def test_build_context_rejects_async_hook_non_list(self, setup):
        _pipeline, plugin = setup

        class BadAsyncContextPlugin:
            state_key = "bad_async_context"

            def hooks(self):
                return {"build_context": self.build_context}

            async def build_context(self, **kwargs):
                del kwargs
                return {"role": "system", "content": "not a list"}

        registry = PluginRegistry()
        registry.register(plugin)
        registry.register(BadAsyncContextPlugin())
        pipeline = Pipeline(runtime=HookRuntime(registry), registry=registry)
        ctx = PipelineContext(tape=Tape(), session_id="s1")

        with pytest.raises(HookTypeError, match="build_context"):
            await pipeline._stage_build_context(ctx)

    @pytest.mark.asyncio
    async def test_build_context_wraps_async_hook_exception(self, setup):
        _pipeline, plugin = setup

        class FailingAsyncContextPlugin:
            state_key = "failing_async_context"

            def hooks(self):
                return {"build_context": self.build_context}

            async def build_context(self, **kwargs):
                del kwargs
                raise RuntimeError("async context boom")

        registry = PluginRegistry()
        registry.register(plugin)
        registry.register(FailingAsyncContextPlugin())
        pipeline = Pipeline(runtime=HookRuntime(registry), registry=registry)
        ctx = PipelineContext(tape=Tape(), session_id="s1")

        with pytest.raises(HookError, match="async context boom") as exc_info:
            await pipeline._stage_build_context(ctx)

        assert exc_info.value.hook_name == "build_context"

    @pytest.mark.asyncio
    async def test_build_context_uses_windowing(self, setup):
        pipeline, plugin = setup
        tape = Tape()
        for i in range(10):
            tape.append(
                Entry(kind="message", payload={"role": "user", "content": f"msg-{i}"})
            )
        ctx = PipelineContext(tape=tape, session_id="s1")

        class WindowPlugin:
            state_key = "window"

            def hooks(self):
                return {"resolve_context_window": self.resolve_context_window}

            def resolve_context_window(self, tape=None, **kwargs):
                if tape is None:
                    return None
                anchor = Entry(
                    kind="anchor",
                    payload={"content": "summary of old entries"},
                    meta={"is_handoff": True, "source_entry_count": 7},
                )
                return (7, anchor)

        window_plugin = WindowPlugin()
        registry = PluginRegistry()
        registry.register(plugin)
        registry.register(window_plugin)
        runtime = HookRuntime(registry)
        pipeline2 = Pipeline(runtime=runtime, registry=registry)

        await pipeline2._stage_build_context(ctx)

        # All original entries preserved + the anchor
        assert len(ctx.tape) == 11  # 10 original + 1 anchor
        # The anchor is in the tape
        anchors = [e for e in ctx.tape if e.kind == "anchor"]
        assert len(anchors) == 1
        assert anchors[0].meta.get("is_handoff")

    @pytest.mark.asyncio
    async def test_build_context_passes_window_start_to_handoff(self, setup):
        pipeline, plugin = setup
        tape = Tape()
        for i in range(10):
            tape.append(
                Entry(kind="message", payload={"role": "user", "content": f"msg-{i}"})
            )
        ctx = PipelineContext(tape=tape, session_id="s1")

        class WindowPlugin:
            state_key = "window2"

            def hooks(self):
                return {"resolve_context_window": self.resolve_context_window}

            def resolve_context_window(self, tape=None, **kwargs):
                anchor = Anchor(
                    anchor_type="handoff",
                    payload={"content": "summary"},
                )
                return (7, anchor)

        registry = PluginRegistry()
        registry.register(plugin)
        registry.register(WindowPlugin())
        pipeline2 = Pipeline(runtime=HookRuntime(registry), registry=registry)

        await pipeline2._stage_build_context(ctx)

        windowed = ctx.tape.windowed_entries()
        assert len(windowed) == 4  # entries[7:10] + anchor
        assert windowed[-1].kind == "anchor"

    @pytest.mark.asyncio
    async def test_build_context_can_advance_window_multiple_times_in_same_turn(
        self, setup
    ):
        pipeline, plugin = setup
        tape = Tape()
        for i in range(12):
            tape.append(
                Entry(kind="message", payload={"role": "user", "content": f"msg-{i}"})
            )
        ctx = PipelineContext(tape=tape, session_id="s1")

        class WindowPlugin:
            state_key = "window-multi"

            def hooks(self):
                return {"resolve_context_window": self.resolve_context_window}

            def resolve_context_window(self, tape=None, **kwargs):
                if tape is None:
                    return None
                visible = tape.windowed_entries()
                if len(visible) > 6:
                    return (
                        len(visible) - 5,
                        Anchor(anchor_type="handoff", payload={"content": "summary"}),
                    )
                return None

        registry = PluginRegistry()
        registry.register(plugin)
        registry.register(WindowPlugin())
        pipeline2 = Pipeline(runtime=HookRuntime(registry), registry=registry)

        await pipeline2._stage_build_context(ctx)
        first_window_start = ctx.tape.window_start

        ctx.tape.append(
            Entry(kind="message", payload={"role": "user", "content": "after-1"})
        )
        ctx.tape.append(
            Entry(kind="message", payload={"role": "assistant", "content": "after-2"})
        )
        await pipeline2._stage_build_context(ctx)

        anchors = [entry for entry in ctx.tape if entry.kind == "anchor"]
        assert len(anchors) == 2
        assert ctx.tape.window_start > first_window_start

    @pytest.mark.asyncio
    async def test_build_context_reentrant_does_not_double_handoff(self, setup):
        pipeline, plugin = setup
        tape = Tape()
        for i in range(10):
            tape.append(
                Entry(kind="message", payload={"role": "user", "content": f"msg-{i}"})
            )
        ctx = PipelineContext(tape=tape, session_id="s1")

        class WindowPlugin:
            state_key = "window3"

            def hooks(self):
                return {"resolve_context_window": self.resolve_context_window}

            def resolve_context_window(self, tape=None, **kwargs):
                anchor = Entry(
                    kind="anchor",
                    payload={"content": "summary"},
                    meta={"is_handoff": True},
                )
                return (7, anchor)

        registry = PluginRegistry()
        registry.register(plugin)
        registry.register(WindowPlugin())
        pipeline2 = Pipeline(runtime=HookRuntime(registry), registry=registry)

        await pipeline2._stage_build_context(ctx)
        await pipeline2._stage_build_context(ctx)

        anchors = [e for e in ctx.tape if e.kind == "anchor"]
        assert len(anchors) == 1

    @pytest.mark.asyncio
    async def test_run_turn_records_one_tool_call_entry_per_call(self, setup):
        pipeline, plugin = setup
        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "do two things"})
        )
        ctx = PipelineContext(tape=tape, session_id="s1")
        await pipeline.mount(ctx)

        from agentkit.providers.models import TextEvent, ToolCallEvent, DoneEvent

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc1", name="file_read", arguments={"path": "a.txt"}
                )
                yield ToolCallEvent(
                    tool_call_id="tc2", name="file_read", arguments={"path": "b.txt"}
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="done")
                yield DoneEvent()

        mock_llm = MagicMock()
        mock_llm.stream = mock_stream
        plugin._mock_llm = mock_llm

        await pipeline.run_turn(ctx)

        tool_call_entries = [e for e in ctx.tape if e.kind == "tool_call"]
        assert len(tool_call_entries) == 2
        assert tool_call_entries[0].payload == {
            "id": "tc1",
            "name": "file_read",
            "arguments": {"path": "a.txt"},
            "role": "assistant",
        }
        assert tool_call_entries[1].payload == {
            "id": "tc2",
            "name": "file_read",
            "arguments": {"path": "b.txt"},
            "role": "assistant",
        }

    @pytest.mark.asyncio
    async def test_run_turn_raises_when_batch_results_are_too_few(self, setup):
        pipeline, plugin = setup
        registry = PluginRegistry()
        registry.register(plugin)
        registry.register(BatchToolPlugin(["only-one-result"]))
        pipeline = Pipeline(runtime=HookRuntime(registry), registry=registry)

        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "do two things"})
        )
        ctx = PipelineContext(tape=tape, session_id="s1")
        await pipeline.mount(ctx)

        from agentkit.providers.models import ToolCallEvent, DoneEvent

        async def mock_stream(messages, tools=None, **kwargs):
            yield ToolCallEvent(
                tool_call_id="tc1", name="file_read", arguments={"path": "a.txt"}
            )
            yield ToolCallEvent(
                tool_call_id="tc2", name="file_read", arguments={"path": "b.txt"}
            )
            yield DoneEvent()

        plugin._mock_llm = MagicMock()
        plugin._mock_llm.stream = mock_stream

        with pytest.raises(
            PipelineError,
            match="execute_tools_batch returned 1 results for 2 tool calls",
        ):
            await pipeline.run_turn(ctx)

    @pytest.mark.asyncio
    async def test_run_turn_raises_when_batch_results_are_too_many(self, setup):
        pipeline, plugin = setup
        registry = PluginRegistry()
        registry.register(plugin)
        registry.register(BatchToolPlugin(["r1", "r2", "r3"]))
        pipeline = Pipeline(runtime=HookRuntime(registry), registry=registry)

        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "do two things"})
        )
        ctx = PipelineContext(tape=tape, session_id="s1")
        await pipeline.mount(ctx)

        from agentkit.providers.models import ToolCallEvent, DoneEvent

        async def mock_stream(messages, tools=None, **kwargs):
            yield ToolCallEvent(
                tool_call_id="tc1", name="file_read", arguments={"path": "a.txt"}
            )
            yield ToolCallEvent(
                tool_call_id="tc2", name="file_read", arguments={"path": "b.txt"}
            )
            yield DoneEvent()

        plugin._mock_llm = MagicMock()
        plugin._mock_llm.stream = mock_stream

        with pytest.raises(
            PipelineError,
            match="execute_tools_batch returned 3 results for 2 tool calls",
        ):
            await pipeline.run_turn(ctx)

    @pytest.mark.asyncio
    async def test_run_turn_reraises_fatal_tool_execution_error_from_batch_hook(
        self, setup
    ):
        _pipeline, plugin = setup
        registry = PluginRegistry()
        registry.register(plugin)
        fatal_plugin = FatalExecuteToolPlugin()
        registry.register(fatal_plugin)
        pipeline = Pipeline(runtime=HookRuntime(registry), registry=registry)

        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "do one thing"})
        )
        ctx = PipelineContext(tape=tape, session_id="s1")
        await pipeline.mount(ctx)

        from agentkit.providers.models import ToolCallEvent, DoneEvent

        async def mock_stream(messages, tools=None, **kwargs):
            del messages, tools, kwargs
            yield ToolCallEvent(tool_call_id="tc1", name="fatal_tool", arguments={})
            yield ToolCallEvent(tool_call_id="tc2", name="fatal_tool", arguments={})
            yield DoneEvent()

        plugin._mock_llm = MagicMock()
        plugin._mock_llm.stream = mock_stream

        with pytest.raises(
            FatalToolExecutionError,
            match="fatal batch tool failure",
        ):
            await pipeline.run_turn(ctx)

        assert fatal_plugin.batch_called is True


class TestPipelineView:
    @pytest.mark.asyncio
    async def test_build_context_uses_view(self):
        registry = PluginRegistry()

        class ViewTestPlugin:
            state_key = "view_test"

            def hooks(self):
                return {
                    "provide_llm": lambda **kw: None,
                    "provide_storage": lambda **kw: None,
                    "get_tools": lambda **kw: [],
                    "build_context": lambda **kw: [],
                }

        registry.register(ViewTestPlugin())
        runtime = HookRuntime(registry)
        pipeline = Pipeline(runtime=runtime, registry=registry)

        tape = Tape()
        for i in range(5):
            tape.append(
                Entry(kind="message", payload={"role": "user", "content": f"old-{i}"})
            )
        anchor = Entry(
            kind="anchor",
            payload={"content": "summary"},
            meta={"is_handoff": True, "prefix": "Summary"},
        )
        tape.handoff(anchor)
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "new msg"})
        )

        ctx = PipelineContext(
            tape=tape,
            session_id="s1",
            config={"system_prompt": "test"},
        )
        await pipeline.mount(ctx)
        await pipeline._stage_build_context(ctx)

        assert len(ctx.messages) == 3
        assert "[Summary]" in ctx.messages[1]["content"]
        assert ctx.messages[2]["content"] == "new msg"
        assert not any("old-" in str(m.get("content", "")) for m in ctx.messages)


@pytest.mark.asyncio
async def test_pipeline_rolls_back_fork_on_cancelled_error():
    class CancellingBuildContextPlugin(MinimalPlugin):
        state_key = "cancelling_build_context"

        def build_context(self, **kwargs):
            del kwargs
            raise asyncio.CancelledError

    registry = PluginRegistry()
    plugin = CancellingBuildContextPlugin()
    registry.register(plugin)
    runtime = HookRuntime(registry)
    pipeline = Pipeline(runtime=runtime, registry=registry)

    class RecordingStorage:
        def __init__(self):
            self.commit_calls = []
            self.rollback_calls = []

        def begin(self, tape):
            return tape.fork()

        async def commit(self, tape):
            self.commit_calls.append(tape)
            return "stable-tape-id"

        def rollback(self, tape):
            self.rollback_calls.append(tape)

    storage = RecordingStorage()
    plugin._mock_storage = storage
    tape = Tape(tape_id="stable-tape-id")
    tape.append(Entry(kind="message", payload={"role": "user", "content": "hello"}))
    ctx = PipelineContext(tape=tape, session_id="s-cancel")
    await pipeline.mount(ctx)

    with pytest.raises(asyncio.CancelledError):
        await pipeline.run_turn(ctx)

    assert storage.commit_calls == []
    assert len(storage.rollback_calls) == 1
    assert storage.rollback_calls[0].parent_id == tape.tape_id
    assert ctx.tape is tape


@pytest.mark.asyncio
async def test_pipeline_preserves_cancelled_error_when_rollback_fails(caplog):
    class CancellingBuildContextPlugin(MinimalPlugin):
        state_key = "cancelling_build_context_with_failed_rollback"

        def build_context(self, **kwargs):
            del kwargs
            raise asyncio.CancelledError

    registry = PluginRegistry()
    plugin = CancellingBuildContextPlugin()
    registry.register(plugin)
    runtime = HookRuntime(registry)
    pipeline = Pipeline(runtime=runtime, registry=registry)

    class FailingRollbackStorage:
        def begin(self, tape):
            return tape.fork()

        async def commit(self, tape):
            del tape
            raise AssertionError("cancelled turns must not commit")

        def rollback(self, tape):
            del tape
            raise RuntimeError("rollback failed")

    plugin._mock_storage = FailingRollbackStorage()
    tape = Tape(tape_id="stable-tape-id")
    tape.append(Entry(kind="message", payload={"role": "user", "content": "hello"}))
    ctx = PipelineContext(tape=tape, session_id="s-cancel")
    await pipeline.mount(ctx)

    with caplog.at_level(logging.ERROR, logger="agentkit.runtime.pipeline"):
        with pytest.raises(asyncio.CancelledError):
            await pipeline.run_turn(ctx)

    assert "Failed to roll back pipeline storage fork" in caplog.text
    assert "rollback failed" in caplog.text
    assert ctx.tape is tape
