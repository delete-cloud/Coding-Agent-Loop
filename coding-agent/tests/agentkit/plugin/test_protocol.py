import pytest
from agentkit.plugin.protocol import Plugin


class DummyPlugin:
    """A minimal plugin implementation for testing."""

    state_key = "dummy"

    def hooks(self) -> dict[str, callable]:
        return {"mount": self.do_mount}

    def do_mount(self) -> dict:
        return {"initialized": True}


class PluginWithoutStateKey:
    """Plugin missing state_key — should fail protocol check."""

    def hooks(self) -> dict[str, callable]:
        return {}


class TestPluginProtocol:
    def test_valid_plugin_satisfies_protocol(self):
        p = DummyPlugin()
        assert isinstance(p, Plugin)

    def test_plugin_state_key(self):
        p = DummyPlugin()
        assert p.state_key == "dummy"

    def test_plugin_hooks_returns_dict(self):
        p = DummyPlugin()
        h = p.hooks()
        assert isinstance(h, dict)
        assert "mount" in h

    def test_missing_state_key_fails_protocol(self):
        p = PluginWithoutStateKey()
        assert not isinstance(p, Plugin)
