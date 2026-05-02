"""Runtime context primitives shared by agentkit pipelines."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from agentkit.environment import Environment


def _validate_token_count(field_name: str, value: int | None) -> None:
    """Reject negative budgets and disallow ``bool`` (a sneaky ``int`` subclass)."""
    if value is None:
        return
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be int or None, got bool")
    if not isinstance(value, int):
        raise TypeError(f"{field_name} must be int or None, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """Context budget limits carried for later context-management enforcement."""

    max_input_tokens: int | None = None
    reserved_output_tokens: int = 0
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        """Validate configured token budgets."""
        _validate_token_count("max_input_tokens", self.max_input_tokens)
        _validate_token_count("reserved_output_tokens", self.reserved_output_tokens)
        _validate_token_count("max_output_tokens", self.max_output_tokens)
        if (
            self.max_output_tokens is not None
            and self.reserved_output_tokens > self.max_output_tokens
        ):
            raise ValueError(
                "reserved_output_tokens must not exceed max_output_tokens"
            )


@dataclass(frozen=True, slots=True)
class AgentRunContext:
    """Runtime identity and dependencies for one agent run.

    ``trace_metadata`` is shallow-frozen via ``MappingProxyType``: top-level keys
    cannot be mutated, but nested mutable values (lists, dicts) are *not*
    deep-copied. Callers must only pass JSON-scalar values (or otherwise
    immutable ones); mutating nested values after construction is undefined
    behavior. ``derive_child`` performs a shallow merge with the same caveat.
    """

    session_id: str
    run_id: str
    agent_id: str | None
    environment: Environment
    parent_run_id: str | None = None
    context_budget: ContextBudget = field(default_factory=ContextBudget)
    trace_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate identity fields and freeze trace metadata."""
        if not self.session_id:
            raise ValueError("session_id must be non-empty")
        if not self.run_id:
            raise ValueError("run_id must be non-empty")
        if self.agent_id == "":
            raise ValueError("agent_id must be None or non-empty")
        if self.parent_run_id == "":
            raise ValueError("parent_run_id must be None or non-empty")
        object.__setattr__(
            self,
            "trace_metadata",
            MappingProxyType(dict(self.trace_metadata)),
        )

    def derive_child(
        self,
        *,
        run_id: str,
        agent_id: str,
        environment: Environment | None = None,
        context_budget: ContextBudget | None = None,
        trace_metadata: Mapping[str, Any] | None = None,
    ) -> AgentRunContext:
        """Create a child run context while preserving session-scoped state."""
        if not run_id:
            raise ValueError("child run_id must be non-empty")
        if not agent_id:
            raise ValueError("child agent_id must be non-empty")
        merged_trace_metadata = dict(self.trace_metadata)
        if trace_metadata is not None:
            merged_trace_metadata.update(trace_metadata)
        return AgentRunContext(
            session_id=self.session_id,
            run_id=run_id,
            agent_id=agent_id,
            environment=self.environment if environment is None else environment,
            parent_run_id=self.run_id,
            context_budget=(
                self.context_budget if context_budget is None else context_budget
            ),
            trace_metadata=merged_trace_metadata,
        )
