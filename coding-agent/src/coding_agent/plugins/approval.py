"""ApprovalPlugin — tool call approval via Directive pattern.

Returns Approve/Reject/AskUser directives based on configured policy.
"""

from __future__ import annotations

from typing import Any, Callable

from coding_agent.approval import ApprovalPolicy
from agentkit.directive.types import Approve, AskUser, Reject


class ApprovalPlugin:
    """Plugin implementing approve_tool_call hook."""

    state_key = "approval"

    def __init__(
        self,
        policy: ApprovalPolicy = ApprovalPolicy.AUTO,
        safe_tools: set[str] | None = None,
        blocked_tools: set[str] | None = None,
        external_request_tools: set[str] | None = None,
    ) -> None:
        self._policy = policy
        self._safe_tools = safe_tools or set()
        self._blocked_tools = blocked_tools or set()
        self._external_request_tools = external_request_tools or {"web_search"}

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {"approve_tool_call": self.approve_tool_call}

    def approve_tool_call(
        self,
        tool_name: str = "",
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Approve | Reject | AskUser:
        """Evaluate a tool call and return an approval directive."""
        if tool_name in self._blocked_tools:
            return Reject(reason=f"tool '{tool_name}' is blocked")

        _meta = {"tool_name": tool_name, "arguments": arguments or {}}

        if tool_name in self._external_request_tools:
            if self._policy == ApprovalPolicy.YOLO:
                return Approve()
            return AskUser(
                question=f"Tool '{tool_name}' performs an external request. Allow?",
                metadata=_meta,
            )

        if self._policy == ApprovalPolicy.YOLO:
            return Approve()
        elif self._policy == ApprovalPolicy.INTERACTIVE:
            return AskUser(
                question=f"Allow tool '{tool_name}' with args {arguments}?",
                metadata=_meta,
            )
        elif self._policy == ApprovalPolicy.AUTO:
            if tool_name in self._safe_tools:
                return Approve()
            return AskUser(
                question=f"Tool '{tool_name}' requires approval. Allow?",
                metadata=_meta,
            )

        return Approve()
