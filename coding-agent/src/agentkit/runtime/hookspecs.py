"""Hook specifications — metadata for the 11 agentkit hooks.

Each HookSpec declares:
  - name: the hook identifier
  - firstresult: if True, runtime uses call_first (stop at first non-None)
  - is_observer: if True, runtime uses notify (fire-and-forget, swallow errors)
  - returns_directive: if True, the return value is a Directive struct
  - doc: human-readable description
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HookSpec:
    """Metadata for a single hook."""

    name: str
    firstresult: bool = False
    is_observer: bool = False
    returns_directive: bool = False
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
        doc="Return Approve/Reject/AskUser directive for a tool call.",
    ),
    "summarize_context": HookSpec(
        name="summarize_context",
        firstresult=True,
        doc="Compress tape entries when context window is exhausted.",
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
    "on_checkpoint": HookSpec(
        name="on_checkpoint",
        is_observer=True,
        doc="Observer: notified at turn boundaries for state persistence.",
    ),
    "build_context": HookSpec(
        name="build_context",
        firstresult=False,
        doc="Inject grounding context (memories, KB results) before prompt build.",
    ),
    "on_turn_end": HookSpec(
        name="on_turn_end",
        firstresult=False,
        returns_directive=True,
        doc="finish_action: produce MemoryRecord directive at turn end.",
    ),
    "execute_tool": HookSpec(
        name="execute_tool",
        firstresult=True,
        doc="Execute a tool by name and return the result. Called by Pipeline.run_model.",
    ),
}
