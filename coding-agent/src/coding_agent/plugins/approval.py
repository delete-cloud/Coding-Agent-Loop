"""ApprovalPlugin — tool call approval via Directive pattern.

Returns Approve/Reject/AskUser directives based on configured policy.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable

from agentkit.directive.types import Approve, AskUser, Reject


class ApprovalPolicy(Enum):
    AUTO = "auto"
    MANUAL = "manual"
    SAFE_ONLY = "safe_only"


class ApprovalPlugin:
    """Plugin implementing approve_tool_call hook."""

    state_key = "approval"

    def __init__(
        self,
        policy: ApprovalPolicy = ApprovalPolicy.AUTO,
        safe_tools: set[str] | None = None,
        blocked_tools: set[str] | None = None,
    ) -> None:
        self._policy = policy
        self._safe_tools = safe_tools or set()
        self._blocked_tools = blocked_tools or set()

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {"approve_tool_call": self.approve_tool_call}

    def approve_tool_call(
        self,
        tool_name: str = "",
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Approve | Reject | AskUser:
        """Evaluate a tool call and return an approval directive."""
        # Always reject blocked tools
        if tool_name in self._blocked_tools:
            return Reject(reason=f"tool '{tool_name}' is blocked")

        if self._policy == ApprovalPolicy.AUTO:
            return Approve()
        elif self._policy == ApprovalPolicy.MANUAL:
            return AskUser(question=f"Allow tool '{tool_name}' with args {arguments}?")
        elif self._policy == ApprovalPolicy.SAFE_ONLY:
            if tool_name in self._safe_tools:
                return Approve()
            return AskUser(question=f"Tool '{tool_name}' requires approval. Allow?")

        return Approve()
