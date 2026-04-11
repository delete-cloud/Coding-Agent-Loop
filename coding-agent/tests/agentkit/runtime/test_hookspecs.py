import pytest
from agentkit.runtime.hookspecs import HOOK_SPECS, HookSpec


class TestHookSpecs:
    def test_all_11_hooks_defined(self):
        expected = {
            "provide_storage",
            "get_tools",
            "provide_llm",
            "approve_tool_call",
            "summarize_context",
            "on_error",
            "mount",
            "on_checkpoint",
            "build_context",
            "on_turn_end",
            "execute_tool",
        }
        assert set(HOOK_SPECS.keys()) == expected

    def test_hookspec_has_required_fields(self):
        for name, spec in HOOK_SPECS.items():
            assert isinstance(spec, HookSpec), f"{name} is not a HookSpec"
            assert isinstance(spec.name, str)
            assert isinstance(spec.firstresult, bool)
            assert isinstance(spec.is_observer, bool)
            assert isinstance(spec.returns_directive, bool)

    def test_provide_hooks_are_firstresult(self):
        assert HOOK_SPECS["provide_storage"].firstresult is True
        assert HOOK_SPECS["provide_llm"].firstresult is True

    def test_get_tools_is_not_firstresult(self):
        assert HOOK_SPECS["get_tools"].firstresult is False

    def test_on_error_is_observer(self):
        assert HOOK_SPECS["on_error"].is_observer is True

    def test_approve_tool_call_returns_directive(self):
        assert HOOK_SPECS["approve_tool_call"].returns_directive is True

    def test_on_turn_end_returns_directive(self):
        assert HOOK_SPECS["on_turn_end"].returns_directive is True

    def test_mount_returns_state(self):
        spec = HOOK_SPECS["mount"]
        assert spec.firstresult is False
        assert spec.is_observer is False

    def test_on_checkpoint_is_observer(self):
        assert HOOK_SPECS["on_checkpoint"].is_observer is True

    def test_build_context_is_not_firstresult(self):
        spec = HOOK_SPECS["build_context"]
        assert spec.firstresult is False
        assert spec.returns_directive is False

    def test_execute_tool_is_firstresult(self):
        spec = HOOK_SPECS["execute_tool"]
        assert spec.firstresult is True
        assert spec.is_observer is False
