import pytest
from agentkit.runtime.hookspecs import HOOK_SPECS, HookSpec
from agentkit.directive.types import Directive


class TestHookSpecs:
    def test_all_15_hooks_defined(self):
        expected = {
            "provide_storage",
            "get_tools",
            "provide_llm",
            "approve_tool_call",
            "summarize_context",
            "resolve_context_window",
            "on_error",
            "mount",
            "on_shutdown",
            "on_checkpoint",
            "build_context",
            "on_turn_end",
            "execute_tool",
            "on_session_event",
            "execute_tools_batch",
        }
        assert set(HOOK_SPECS.keys()) == expected

    def test_hookspec_has_required_fields(self):
        for name, spec in HOOK_SPECS.items():
            assert isinstance(spec, HookSpec), f"{name} is not a HookSpec"
            assert isinstance(spec.name, str)
            assert isinstance(spec.firstresult, bool)
            assert isinstance(spec.is_observer, bool)
            assert isinstance(spec.returns_directive, bool)
            assert spec.return_type is None or isinstance(spec.return_type, type)

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

    def test_on_session_event_is_observer(self):
        spec = HOOK_SPECS["on_session_event"]
        assert spec.is_observer is True
        assert spec.firstresult is False
        assert spec.returns_directive is False

    def test_execute_tools_batch_is_firstresult(self):
        spec = HOOK_SPECS["execute_tools_batch"]
        assert spec.firstresult is True
        assert spec.is_observer is False
        assert spec.return_type is None


class TestHookSpecReturnTypes:
    def test_approve_tool_call_declares_directive_return(self):
        spec = HOOK_SPECS["approve_tool_call"]
        assert spec.return_type is Directive

    def test_on_turn_end_declares_directive_return(self):
        spec = HOOK_SPECS["on_turn_end"]
        assert spec.return_type is Directive

    def test_resolve_context_window_declares_tuple_return(self):
        spec = HOOK_SPECS["resolve_context_window"]
        assert spec.return_type is tuple

    def test_provide_llm_has_no_return_type(self):
        spec = HOOK_SPECS["provide_llm"]
        assert spec.return_type is None

    def test_observer_hooks_have_no_return_type(self):
        for name in ("on_error", "on_checkpoint", "on_session_event", "on_shutdown"):
            spec = HOOK_SPECS[name]
            assert spec.return_type is None, f"{name} should not declare return_type"

    def test_every_hook_with_returns_directive_has_directive_return_type(self):
        for name, spec in HOOK_SPECS.items():
            if spec.returns_directive:
                assert spec.return_type is Directive, (
                    f"{name} has returns_directive=True but return_type is not Directive"
                )
