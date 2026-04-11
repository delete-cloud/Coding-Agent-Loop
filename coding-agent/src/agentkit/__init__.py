"""agentkit — A hook-driven agent framework.

Core API:
    HookRuntime, Pipeline, PipelineContext — Runtime and execution
    Plugin, PluginRegistry — Plugin system
    Directive, Approve, Reject, AskUser, Checkpoint, MemoryRecord — Effect descriptions
    Entry, Tape, ForkTapeStore — Conversation history
    TapeStore, DocIndex, SessionStore — Storage protocols
    ToolSchema, ToolRegistry, tool — Tool system
    Channel, LocalChannel — Communication
    AgentConfig, load_config — Configuration
    ContextBuilder — Message assembly
    normalize_instruction — Input normalization
"""

from agentkit.channel import Channel, LocalChannel
from agentkit.config import AgentConfig, load_config
from agentkit.context import ContextBuilder
from agentkit.directive import (
    Approve,
    AskUser,
    Checkpoint,
    Directive,
    DirectiveExecutor,
    MemoryRecord,
    Reject,
)
from agentkit.errors import (
    AgentKitError,
    ConfigError,
    DirectiveError,
    HookError,
    PipelineError,
    PluginError,
    StorageError,
    ToolError,
)
from agentkit.instruction import normalize_instruction
from agentkit.plugin import Plugin, PluginRegistry
from agentkit.providers import (
    DoneEvent,
    LLMProvider,
    StreamEvent,
    TextEvent,
    ToolCallEvent,
)
from agentkit.runtime import HookRuntime, Pipeline, PipelineContext
from agentkit.storage import DocIndex, SessionStore, TapeStore
from agentkit.tape import Entry, ForkTapeStore, Tape
from agentkit.tools import ToolRegistry, ToolSchema, tool

__all__ = [
    # Runtime
    "HookRuntime",
    "Pipeline",
    "PipelineContext",
    # Plugins
    "Plugin",
    "PluginRegistry",
    # Directives
    "Directive",
    "DirectiveExecutor",
    "Approve",
    "Reject",
    "AskUser",
    "Checkpoint",
    "MemoryRecord",
    # Tape
    "Entry",
    "Tape",
    "ForkTapeStore",
    # Storage
    "TapeStore",
    "DocIndex",
    "SessionStore",
    # Tools
    "ToolSchema",
    "ToolRegistry",
    "tool",
    # Providers
    "LLMProvider",
    "StreamEvent",
    "TextEvent",
    "ToolCallEvent",
    "DoneEvent",
    # Channel
    "Channel",
    "LocalChannel",
    # Config
    "AgentConfig",
    "load_config",
    # Context
    "ContextBuilder",
    # Instruction
    "normalize_instruction",
    # Errors
    "AgentKitError",
    "HookError",
    "PipelineError",
    "PluginError",
    "DirectiveError",
    "StorageError",
    "ToolError",
    "ConfigError",
]
