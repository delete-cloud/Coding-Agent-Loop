import pytest
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.plugin.registry import PluginRegistry
from agentkit.errors import HookError, HookTypeError
from agentkit.runtime.hookspecs import HOOK_SPECS
from agentkit.directive.types import Approve, Directive


def test_hook_type_error_has_hook_name_and_detail():
    err = HookTypeError(
        "expected Directive, got dict",
        hook_name="approve_tool_call",
    )
    assert err.hook_name == "approve_tool_call"
    assert "expected Directive, got dict" in str(err)


class ProviderPlugin:
    state_key = "provider"

    def hooks(self):
        return {"provide_llm": self.provide_llm}

    def provide_llm(self, **kwargs):
        return {"model": "gpt-4"}


class FallbackProviderPlugin:
    state_key = "fallback"

    def hooks(self):
        return {"provide_llm": self.provide_llm}

    def provide_llm(self, **kwargs):
        return {"model": "claude"}


class ToolPluginA:
    state_key = "tools_a"

    def hooks(self):
        return {"get_tools": self.get_tools}

    def get_tools(self, **kwargs):
        return [{"name": "bash"}]


class ToolPluginB:
    state_key = "tools_b"

    def hooks(self):
        return {"get_tools": self.get_tools}

    def get_tools(self, **kwargs):
        return [{"name": "file_read"}]


class ErrorPlugin:
    state_key = "error"

    def hooks(self):
        return {"on_error": self.on_error}

    def on_error(self, **kwargs):
        raise RuntimeError("observer failure should be swallowed")


class BrokenPlugin:
    state_key = "broken"

    def hooks(self):
        return {"provide_llm": self.provide_llm}

    def provide_llm(self, **kwargs):
        raise ValueError("hook crashed")


class NonePlugin:
    state_key = "none_provider"

    def hooks(self):
        return {"provide_llm": self.provide_llm}

    def provide_llm(self, **kwargs):
        return None


class TestHookRuntime:
    @pytest.fixture
    def registry(self):
        return PluginRegistry()

    @pytest.fixture
    def runtime(self, registry):
        return HookRuntime(registry)

    def test_call_first_returns_first_non_none(self, registry, runtime):
        registry.register(NonePlugin())
        registry.register(ProviderPlugin())
        registry.register(FallbackProviderPlugin())
        result = runtime.call_first("provide_llm")
        assert result == {"model": "gpt-4"}

    def test_call_first_returns_none_when_no_hooks(self, runtime):
        result = runtime.call_first("nonexistent_hook")
        assert result is None

    def test_call_first_skips_none_returns(self, registry, runtime):
        registry.register(NonePlugin())
        registry.register(ProviderPlugin())
        result = runtime.call_first("provide_llm")
        assert result == {"model": "gpt-4"}

    def test_call_many_collects_all(self, registry, runtime):
        registry.register(ToolPluginA())
        registry.register(ToolPluginB())
        results = runtime.call_many("get_tools")
        assert len(results) == 2
        names = [r[0]["name"] for r in results]
        assert "bash" in names
        assert "file_read" in names

    def test_call_many_empty(self, runtime):
        results = runtime.call_many("nonexistent")
        assert results == []

    def test_notify_swallows_errors(self, registry, runtime):
        registry.register(ErrorPlugin())
        runtime.notify("on_error", error="test")

    def test_call_first_propagates_errors(self, registry, runtime):
        registry.register(BrokenPlugin())
        with pytest.raises(HookError, match="hook crashed"):
            runtime.call_first("provide_llm")

    def test_call_first_passes_kwargs(self, registry, runtime):
        class KwargsPlugin:
            state_key = "kwargs"

            def hooks(self):
                return {"custom": self.custom}

            def custom(self, **kwargs):
                return kwargs.get("x", 0) + kwargs.get("y", 0)

        registry.register(KwargsPlugin())
        result = runtime.call_first("custom", x=3, y=4)
        assert result == 7


class BadReturnPlugin:
    state_key = "bad_return"

    def hooks(self):
        return {"approve_tool_call": self.approve_tool_call}

    def approve_tool_call(self, **kwargs):
        return {"approved": True}


class GoodDirectivePlugin:
    state_key = "good_directive"

    def hooks(self):
        return {"approve_tool_call": self.approve_tool_call}

    def approve_tool_call(self, **kwargs):
        return Approve()


class BadTuplePlugin:
    state_key = "bad_tuple"

    def hooks(self):
        return {"resolve_context_window": self.resolve}

    def resolve(self, **kwargs):
        return "not a tuple"


class TestHookRuntimeTypeValidation:
    @pytest.fixture
    def registry(self):
        return PluginRegistry()

    @pytest.fixture
    def runtime(self, registry):
        return HookRuntime(registry, specs=HOOK_SPECS)

    def test_call_first_raises_on_wrong_return_type(self, registry, runtime):
        registry.register(BadReturnPlugin())
        with pytest.raises(HookTypeError, match="approve_tool_call"):
            runtime.call_first("approve_tool_call", tool_name="bash", arguments={})

    def test_call_first_accepts_correct_directive(self, registry, runtime):
        registry.register(GoodDirectivePlugin())
        result = runtime.call_first("approve_tool_call", tool_name="bash", arguments={})
        assert isinstance(result, Approve)

    def test_call_first_accepts_none_even_with_return_type(self, registry, runtime):
        registry.register(NonePlugin())
        result = runtime.call_first("provide_llm")
        assert result is None

    def test_call_first_skips_validation_for_unknown_hook(self, registry, runtime):
        class CustomPlugin:
            state_key = "custom"

            def hooks(self):
                return {"my_custom_hook": lambda **kw: "anything"}

        registry.register(CustomPlugin())
        result = runtime.call_first("my_custom_hook")
        assert result == "anything"

    def test_call_first_validates_tuple_return(self, registry, runtime):
        registry.register(BadTuplePlugin())
        with pytest.raises(HookTypeError, match="resolve_context_window"):
            runtime.call_first("resolve_context_window", tape=None)

    def test_call_many_validates_each_result(self, registry, runtime):
        class BadContextPlugin:
            state_key = "bad_ctx"

            def hooks(self):
                return {"build_context": self.build_context}

            def build_context(self, **kwargs):
                return "not a list"

        registry.register(BadContextPlugin())
        with pytest.raises(HookTypeError, match="build_context"):
            runtime.call_many("build_context", tape=None)

    def test_call_many_accepts_correct_list(self, registry, runtime):
        class GoodContextPlugin:
            state_key = "good_ctx"

            def hooks(self):
                return {"build_context": self.build_context}

            def build_context(self, **kwargs):
                return [{"role": "system", "content": "memory"}]

        registry.register(GoodContextPlugin())
        results = runtime.call_many("build_context", tape=None)
        assert len(results) == 1

    def test_hook_runtime_no_specs_skips_validation(self, registry):
        runtime_no_specs = HookRuntime(registry)
        registry.register(BadReturnPlugin())
        result = runtime_no_specs.call_first(
            "approve_tool_call", tool_name="bash", arguments={}
        )
        assert result == {"approved": True}
