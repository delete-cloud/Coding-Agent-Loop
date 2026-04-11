"""Hook specifications — metadata for the 14 agentkit hooks.

Each HookSpec declares:
  - name: the hook identifier
  - firstresult: if True, runtime uses call_first (stop at first non-None)
  - is_observer: if True, runtime uses notify (fire-and-forget, swallow errors)
  - returns_directive: if True, the return value is a Directive struct
  - return_type: expected Python type of a non-None return value, or None to skip validation
  - doc: human-readable description
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HookSpec:
    """Metadata for a single hook."""

    name: str
    firstresult: bool = False
    is_observer: bool = False
    returns_directive: bool = False
    return_type: type | None = field(default=None)
    doc: str = ""


HOOK_SPECS: dict[str, HookSpec] = {
    "provide_storage": HookSpec(
        name="provide_storage",
        firstresult=True,
        doc="Return a TapeStore instance (with optional ForkTapeStore wrapping).",
    ),
    "get_tools": HookSpec(
        name="get_tools",
        firstresult=False,
        doc="Collect tool schemas from all plugins. call_many gathers lists.",
    ),
    "provide_llm": HookSpec(
        name="provide_llm",
        firstresult=True,
        doc="Return an LLMProvider instance for the current session.",
    ),
    "approve_tool_call": HookSpec(
        name="approve_tool_call",
        firstresult=True,
        returns_directive=True,
        return_type=None,  # set dynamically below to avoid circular import
        doc="Return Approve/Reject/AskUser directive for a tool call.",
    ),
    "summarize_context": HookSpec(
        name="summarize_context",
        firstresult=True,
        doc="Compress tape entries when context window is exhausted.",
    ),
    "resolve_context_window": HookSpec(
        name="resolve_context_window",
        firstresult=True,
        return_type=tuple,
        doc="Determine context window boundaries. Returns (window_start_index, summary_anchor_entry) "
        "or None if no windowing needed. Original entries are always preserved.",
    ),
    "on_error": HookSpec(
        name="on_error",
        is_observer=True,
        doc="Observer: notified on pipeline errors. Cannot affect flow.",
    ),
    "mount": HookSpec(
        name="mount",
        firstresult=False,
        doc="Plugin initialization. Returns initial plugin state dict.",
    ),
    "on_shutdown": HookSpec(
        name="on_shutdown",
        is_observer=True,
        doc="Observer: notified when a pipeline instance is shutting down.",
    ),
    "on_checkpoint": HookSpec(
        name="on_checkpoint",
        is_observer=True,
        doc="Observer: notified at turn boundaries for state persistence.",
    ),
    "build_context": HookSpec(
        name="build_context",
        firstresult=False,
        return_type=list,
        doc="Inject grounding context (memories, KB results) before prompt build.",
    ),
    "on_turn_end": HookSpec(
        name="on_turn_end",
        firstresult=False,
        returns_directive=True,
        return_type=None,  # set dynamically below to avoid circular import
        doc="finish_action: produce MemoryRecord directive at turn end.",
    ),
    "execute_tool": HookSpec(
        name="execute_tool",
        firstresult=True,
        doc="Execute a tool by name and return the result. Called by Pipeline.run_model.",
    ),
    "on_session_event": HookSpec(
        name="on_session_event",
        is_observer=True,
        doc="Observer: notified on session-level events (topic_start, topic_end, handoff, etc). "
        "Receives event_type: str and payload: dict. Cannot affect pipeline flow.",
    ),
    "execute_tools_batch": HookSpec(
        name="execute_tools_batch",
        firstresult=True,
        doc="Execute a batch of tool calls in parallel. Returns list of results.",
    ),
}


def _patch_directive_return_types() -> None:
    from agentkit.directive.types import Directive  # local import to avoid circular

    object.__setattr__(HOOK_SPECS["approve_tool_call"], "return_type", Directive)
    object.__setattr__(HOOK_SPECS["on_turn_end"], "return_type", Directive)


_patch_directive_return_types()
