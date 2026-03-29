"""Shared type aliases used across agentkit."""

from __future__ import annotations

from typing import Any, Literal

HookName = str
PluginId = str

EntryKind = Literal["message", "tool_call", "tool_result", "anchor", "event"]

Role = Literal["system", "user", "assistant", "tool"]

JsonDict = dict[str, Any]

StageName = Literal[
    "resolve_session",
    "load_state",
    "build_context",
    "run_model",
    "save_state",
    "render",
    "dispatch",
]
