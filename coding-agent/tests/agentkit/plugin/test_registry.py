import warnings

import pytest

from agentkit.errors import PluginError
from agentkit.plugin import PluginCapability
from agentkit.plugin.registry import PluginRegistry
from agentkit.runtime.hookspecs import HOOK_SPECS


class FakePluginA:
    state_key = "alpha"

    def hooks(self):
        return {"mount": self.do_mount, "get_tools": self.get_tools}

    def do_mount(self):
        return {"ready": True}

    def get_tools(self):
        return []


class FakePluginB:
    state_key = "beta"

    def hooks(self):
        return {"mount": self.do_mount}

    def do_mount(self):
        return {}


class InvalidPlugin:
    """Not a valid plugin — missing state_key."""

    def hooks(self):
        return {}


class TestPluginRegistry:
    def test_register_plugin(self):
        reg = PluginRegistry()
        reg.register(FakePluginA())
        assert "alpha" in reg.plugin_ids()

    def test_register_multiple(self):
        reg = PluginRegistry()
        reg.register(FakePluginA())
        reg.register(FakePluginB())
        assert reg.plugin_ids() == ["alpha", "beta"]

    def test_duplicate_state_key_raises(self):
        reg = PluginRegistry()
        reg.register(FakePluginA())
        with pytest.raises(PluginError, match="duplicate state_key"):
            reg.register(FakePluginA())

    def test_invalid_plugin_raises(self):
        reg = PluginRegistry()
        with pytest.raises(PluginError, match="does not satisfy Plugin protocol"):
            reg.register(InvalidPlugin())  # type: ignore[arg-type]

    def test_get_hooks_for_name(self):
        reg = PluginRegistry()
        reg.register(FakePluginA())
        reg.register(FakePluginB())
        mount_hooks = reg.get_hooks("mount")
        assert len(mount_hooks) == 2

    def test_get_hooks_for_missing_name(self):
        reg = PluginRegistry()
        reg.register(FakePluginA())
        hooks = reg.get_hooks("nonexistent")
        assert hooks == []

    def test_get_plugin_by_id(self):
        reg = PluginRegistry()
        plugin = FakePluginA()
        reg.register(plugin)
        assert reg.get("alpha") is plugin

    def test_get_missing_plugin_raises(self):
        reg = PluginRegistry()
        with pytest.raises(PluginError, match="not found"):
            reg.get("nonexistent")

    @pytest.mark.parametrize(
        ("capability", "hook_name"),
        [
            (PluginCapability.PENDING_FACT, "build_context"),
            (PluginCapability.EFFECT_PLAN, "get_tools"),
            (PluginCapability.OBSERVER, "on_checkpoint"),
        ],
    )
    def test_capability_declared_plugin_registers_only_allowed_hooks(
        self, capability, hook_name
    ):
        class ClassifiedPlugin:
            state_key = f"classified_{capability.value}"
            capabilities = frozenset({capability})

            def hooks(self):
                return {hook_name: lambda **kwargs: None}

        registry = PluginRegistry(specs=HOOK_SPECS)
        registry.register(ClassifiedPlugin())

        assert len(registry.get_hooks(hook_name)) == 1

    @pytest.mark.parametrize(
        "host_hook",
        [
            "provide_storage",
            "provide_llm",
            "approve_tool_call",
            "execute_tool",
            "execute_proxy_tool",
            "execute_tools_batch",
        ],
    )
    def test_capability_declared_plugin_cannot_register_host_hook(self, host_hook):
        class CapabilityMisusePlugin:
            state_key = f"misuse_{host_hook}"
            capabilities = frozenset({PluginCapability.EFFECT_PLAN})

            def hooks(self):
                return {
                    "get_tools": lambda **kwargs: [],
                    host_hook: lambda **kwargs: None,
                }

        registry = PluginRegistry(specs=HOOK_SPECS)

        with pytest.raises(PluginError, match=host_hook):
            registry.register(CapabilityMisusePlugin())

        assert registry.plugin_ids() == []
        assert registry.get_hooks("get_tools") == []

    def test_unclassified_legacy_plugin_keeps_host_hooks(self):
        plugin = SingleLegacyExecutionPlugin()
        registry = PluginRegistry(specs=HOOK_SPECS)

        registry.register(plugin)

        assert registry.get_hooks("execute_tool") == [plugin.execute_tool]


class SingleLegacyExecutionPlugin:
    state_key = "legacy_execution"

    def hooks(self):
        return {"execute_tool": self.execute_tool}

    def execute_tool(self, **kwargs):
        del kwargs
        return "legacy"


class TestRegistryUnknownHookWarning:
    def test_unknown_hook_emits_warning(self):
        class UnknownHookPlugin:
            state_key = "unknown_hooks"

            def hooks(self):
                return {"totally_made_up_hook": lambda **kw: None}

        registry = PluginRegistry(specs=HOOK_SPECS)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            registry.register(UnknownHookPlugin())
        assert len(w) == 1
        assert "totally_made_up_hook" in str(w[0].message)
        assert issubclass(w[0].category, UserWarning)

    def test_known_hook_no_warning(self):
        class KnownHookPlugin:
            state_key = "known_hooks"

            def hooks(self):
                return {"on_error": lambda **kw: None}

        registry = PluginRegistry(specs=HOOK_SPECS)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            registry.register(KnownHookPlugin())
        assert len(w) == 0

    def test_no_specs_no_warning(self):
        class UnknownHookPlugin:
            state_key = "unknown_hooks_no_specs"

            def hooks(self):
                return {"totally_made_up_hook": lambda **kw: None}

        registry = PluginRegistry()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            registry.register(UnknownHookPlugin())
        assert len(w) == 0
