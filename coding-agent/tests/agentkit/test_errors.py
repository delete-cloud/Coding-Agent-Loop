import pytest
from agentkit.errors import (
    AgentKitError,
    HookError,
    PipelineError,
    PluginError,
    DirectiveError,
    StorageError,
    ToolError,
    ConfigError,
)


class TestErrorHierarchy:
    def test_all_errors_inherit_from_base(self):
        for cls in [
            HookError,
            PipelineError,
            PluginError,
            DirectiveError,
            StorageError,
            ToolError,
            ConfigError,
        ]:
            err = cls("test")
            assert isinstance(err, AgentKitError)
            assert isinstance(err, Exception)

    def test_error_message_preserved(self):
        err = HookError("hook 'provide_llm' failed")
        assert str(err) == "hook 'provide_llm' failed"

    def test_hook_error_captures_hook_name(self):
        err = HookError("failed", hook_name="provide_llm")
        assert err.hook_name == "provide_llm"

    def test_plugin_error_captures_plugin_id(self):
        err = PluginError("failed", plugin_id="memory")
        assert err.plugin_id == "memory"

    def test_pipeline_error_captures_stage(self):
        err = PipelineError("failed", stage="load_state")
        assert err.stage == "load_state"
