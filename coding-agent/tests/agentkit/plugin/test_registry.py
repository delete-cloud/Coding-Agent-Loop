import pytest
import warnings
from agentkit.plugin.registry import PluginRegistry
from agentkit.plugin.protocol import Plugin
from agentkit.errors import PluginError
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
