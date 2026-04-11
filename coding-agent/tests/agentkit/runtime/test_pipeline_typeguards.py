import pytest
from unittest.mock import AsyncMock, MagicMock

from agentkit.directive.types import Approve, Directive
from agentkit.errors import HookTypeError
from agentkit.plugin.registry import PluginRegistry
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.runtime.hookspecs import HOOK_SPECS
from agentkit.runtime.pipeline import Pipeline, PipelineContext
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape


def _make_tape(n: int = 5) -> Tape:
    t = Tape()
    for i in range(n):
        t.append(Entry(kind="message", payload={"role": "user", "content": f"msg {i}"}))
    return t


class TestPipelineTypeGuards:
    @pytest.mark.asyncio
    async def test_build_context_skips_non_tuple_window_result(self):
        registry = PluginRegistry()

        class BadWindowPlugin:
            state_key = "bad_window"

            def hooks(self):
                return {"resolve_context_window": self.resolve}

            def resolve(self, **kwargs):
                return "not a tuple"

        registry.register(BadWindowPlugin())
        runtime = HookRuntime(registry)
        pipeline = Pipeline(runtime=runtime, registry=registry)

        tape = _make_tape()
        ctx = PipelineContext(tape=tape, config={"system_prompt": "test"})

        await pipeline._stage_build_context(ctx)
        assert len(ctx.tape.windowed_entries()) == 5

    @pytest.mark.asyncio
    async def test_build_context_skips_tuple_wrong_length(self):
        registry = PluginRegistry()

        class ShortTuplePlugin:
            state_key = "short_tuple"

            def hooks(self):
                return {"resolve_context_window": self.resolve}

            def resolve(self, **kwargs):
                return (1,)

        registry.register(ShortTuplePlugin())
        runtime = HookRuntime(registry)
        pipeline = Pipeline(runtime=runtime, registry=registry)

        tape = _make_tape()
        ctx = PipelineContext(tape=tape, config={"system_prompt": "test"})

        await pipeline._stage_build_context(ctx)
        assert len(ctx.tape.windowed_entries()) == 5

    @pytest.mark.asyncio
    async def test_build_context_skips_tuple_non_int_first(self):
        registry = PluginRegistry()

        class BadFirstPlugin:
            state_key = "bad_first"

            def hooks(self):
                return {"resolve_context_window": self.resolve}

            def resolve(self, **kwargs):
                return ("not_int", None)

        registry.register(BadFirstPlugin())
        runtime = HookRuntime(registry)
        pipeline = Pipeline(runtime=runtime, registry=registry)

        tape = _make_tape()
        ctx = PipelineContext(tape=tape, config={"system_prompt": "test"})

        await pipeline._stage_build_context(ctx)
        assert len(ctx.tape.windowed_entries()) == 5

    @pytest.mark.asyncio
    async def test_build_context_accepts_valid_tuple(self):
        registry = PluginRegistry()

        class GoodWindowPlugin:
            state_key = "good_window"

            def hooks(self):
                return {"resolve_context_window": self.resolve}

            def resolve(self, **kwargs):
                return (0, None)

        registry.register(GoodWindowPlugin())
        runtime = HookRuntime(registry)
        pipeline = Pipeline(runtime=runtime, registry=registry)

        tape = _make_tape()
        ctx = PipelineContext(tape=tape, config={"system_prompt": "test"})

        await pipeline._stage_build_context(ctx)
        assert len(ctx.tape.windowed_entries()) == 5

    @pytest.mark.asyncio
    async def test_render_skips_non_directive(self):
        registry = PluginRegistry()

        class BadTurnEndPlugin:
            state_key = "bad_turn"

            def hooks(self):
                return {"on_turn_end": self.on_turn_end}

            def on_turn_end(self, **kwargs):
                return {"not": "a directive"}

        registry.register(BadTurnEndPlugin())
        runtime = HookRuntime(registry)

        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock(return_value=None)

        pipeline = Pipeline(
            runtime=runtime, registry=registry, directive_executor=mock_executor
        )
        ctx = PipelineContext(tape=_make_tape())

        await pipeline._stage_render(ctx)
        mock_executor.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_render_stores_only_valid_directives_in_output(self):
        registry = PluginRegistry()

        class MixedPlugin:
            state_key = "mixed"

            def hooks(self):
                return {"on_turn_end": self.on_turn_end}

            def on_turn_end(self, **kwargs):
                return {"bad": "dict"}

        registry.register(MixedPlugin())
        runtime = HookRuntime(registry)
        pipeline = Pipeline(runtime=runtime, registry=registry)
        ctx = PipelineContext(tape=_make_tape())

        await pipeline._stage_render(ctx)
        assert ctx.output == {"directives": []}

    @pytest.mark.asyncio
    async def test_render_passes_valid_directive_to_executor(self):
        registry = PluginRegistry()

        class GoodTurnEndPlugin:
            state_key = "good_turn"

            def hooks(self):
                return {"on_turn_end": self.on_turn_end}

            def on_turn_end(self, **kwargs):
                return Approve()

        registry.register(GoodTurnEndPlugin())
        runtime = HookRuntime(registry)

        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock(return_value=True)

        pipeline = Pipeline(
            runtime=runtime, registry=registry, directive_executor=mock_executor
        )
        ctx = PipelineContext(tape=_make_tape())

        await pipeline._stage_render(ctx)
        assert mock_executor.execute.call_count == 1
        assert ctx.output == {"directives": [Approve()]}


class TestEndToEndTypeEnforcement:
    def test_bad_directive_caught_by_runtime_before_pipeline(self):
        registry = PluginRegistry()

        class BadApprovalPlugin:
            state_key = "bad_approval"

            def hooks(self):
                return {"approve_tool_call": self.approve}

            def approve(self, **kwargs):
                return {"bad": "dict"}

        registry.register(BadApprovalPlugin())
        runtime = HookRuntime(registry, specs=HOOK_SPECS)

        with pytest.raises(HookTypeError, match="approve_tool_call"):
            runtime.call_first("approve_tool_call", tool_name="bash", arguments={})

    def test_good_plugin_passes_both_layers(self):
        registry = PluginRegistry(specs=HOOK_SPECS)

        class GoodApprovalPlugin:
            state_key = "good"

            def hooks(self):
                return {"approve_tool_call": self.approve}

            def approve(self, **kwargs):
                return Approve()

        registry.register(GoodApprovalPlugin())
        runtime = HookRuntime(registry, specs=HOOK_SPECS)

        result = runtime.call_first("approve_tool_call", tool_name="bash", arguments={})
        assert isinstance(result, Approve)

    def test_unknown_hook_has_no_validation(self):
        registry = PluginRegistry(specs=HOOK_SPECS)

        class ExtensionPlugin:
            state_key = "ext"

            def hooks(self):
                return {"custom_analysis": self.analyze}

            def analyze(self, **kwargs):
                return {"anything": True}

        registry.register(ExtensionPlugin())
        runtime = HookRuntime(registry, specs=HOOK_SPECS)
        result = runtime.call_first("custom_analysis")
        assert result == {"anything": True}

    def test_none_always_allowed_with_specs(self):
        registry = PluginRegistry(specs=HOOK_SPECS)

        class NoneApprovalPlugin:
            state_key = "none_approval"

            def hooks(self):
                return {"approve_tool_call": self.approve}

            def approve(self, **kwargs):
                return None

        registry.register(NoneApprovalPlugin())
        runtime = HookRuntime(registry, specs=HOOK_SPECS)
        result = runtime.call_first("approve_tool_call", tool_name="bash", arguments={})
        assert result is None
