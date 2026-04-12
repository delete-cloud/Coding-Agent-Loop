"""agentkit error hierarchy.

All framework errors inherit from AgentKitError.
Domain-specific errors carry contextual attributes (hook_name, plugin_id, stage).
"""

from __future__ import annotations


class AgentKitError(Exception):
    """Base error for all agentkit exceptions."""


class HookError(AgentKitError):
    """A hook invocation failed."""

    def __init__(self, message: str, *, hook_name: str | None = None) -> None:
        super().__init__(message)
        self.hook_name = hook_name


class HookTypeError(HookError):
    pass


class PipelineError(AgentKitError):
    """A pipeline stage failed."""

    def __init__(self, message: str, *, stage: str | None = None) -> None:
        super().__init__(message)
        self.stage = stage


class PluginError(AgentKitError):
    """Plugin initialization or lifecycle error."""

    def __init__(self, message: str, *, plugin_id: str | None = None) -> None:
        super().__init__(message)
        self.plugin_id = plugin_id


class DirectiveError(AgentKitError):
    """Directive execution failed."""


class StorageError(AgentKitError):
    """Storage operation failed."""


class ToolError(AgentKitError):
    """Tool execution failed."""


class ConfigError(AgentKitError):
    """Configuration loading or validation error."""
