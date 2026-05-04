from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields
from typing import Any, cast

import pytest

from agentkit.environment import WorkspaceSummary
from agentkit.runtime import AgentRunContext, ContextBudget


class StubEnvironment:
    @property
    def kind(self) -> str:
        return "stub"

    def tool_config(self) -> dict[str, Any]:
        return {"workspace_root": "/tmp/workspace"}

    def workspace_summary(self) -> WorkspaceSummary:
        return WorkspaceSummary(
            display_name="/tmp/workspace",
            default_cwd="/tmp/workspace",
            local_root="/tmp/workspace",
        )

    def build_file_tools(self) -> tuple[
        Callable[[str], str | dict[str, Any]],
        Callable[[str, str], str],
        Callable[[str, str, str], str],
        Callable[[str, str], str],
        Callable[[str, str, str], str | dict[str, Any]],
    ]:
        raise NotImplementedError

    def build_file_patch_tool(self) -> Callable[[str, str], str]:
        raise NotImplementedError

    def build_shell_tool(self) -> Callable[..., Any]:
        raise NotImplementedError


def test_agent_run_context_carries_runtime_identity_without_model_source() -> None:
    environment = StubEnvironment()
    budget = ContextBudget(max_input_tokens=128000, reserved_output_tokens=4096)
    run_context = AgentRunContext(
        session_id="session-1",
        run_id="run-1",
        agent_id="main",
        environment=environment,
        context_budget=budget,
        trace_metadata={"request_id": "req-1"},
    )

    assert run_context.session_id == "session-1"
    assert run_context.run_id == "run-1"
    assert run_context.agent_id == "main"
    assert run_context.parent_run_id is None
    assert run_context.environment is environment
    assert run_context.context_budget is budget
    assert run_context.trace_metadata["request_id"] == "req-1"


def test_agent_run_context_schema_excludes_model_source_fields() -> None:
    """Identity primitives must not silently absorb provider/model metadata."""
    field_names = {field.name for field in fields(AgentRunContext)}

    required = {"session_id", "run_id", "agent_id", "environment"}
    assert required <= field_names

    forbidden = {"provider_name", "model_name", "base_url", "max_steps"}
    assert forbidden.isdisjoint(field_names)


def test_agent_run_context_allows_none_for_root_agent_id() -> None:
    run_context = AgentRunContext(
        session_id="session-1",
        run_id="run-1",
        agent_id=None,
        environment=StubEnvironment(),
    )

    assert run_context.agent_id is None


def test_agent_run_context_rejects_empty_agent_id() -> None:
    with pytest.raises(ValueError, match="agent_id must be None or non-empty"):
        AgentRunContext(
            session_id="session-1",
            run_id="run-1",
            agent_id="",
            environment=StubEnvironment(),
        )


def test_agent_run_context_defensively_copies_trace_metadata() -> None:
    environment = StubEnvironment()
    source_metadata = {"request_id": "req-1"}

    run_context = AgentRunContext(
        session_id="session-1",
        run_id="run-1",
        agent_id="main",
        environment=environment,
        trace_metadata=source_metadata,
    )

    source_metadata["request_id"] = "mutated"

    assert run_context.trace_metadata["request_id"] == "req-1"
    with pytest.raises(TypeError):
        cast(dict[str, Any], run_context.trace_metadata)["new"] = "value"


def test_agent_run_context_derives_child_run_from_parent() -> None:
    environment = StubEnvironment()
    budget = ContextBudget(max_input_tokens=64000)
    parent = AgentRunContext(
        session_id="session-1",
        run_id="parent-run",
        agent_id="parent-agent",
        environment=environment,
        context_budget=budget,
        trace_metadata={"request_id": "req-1"},
    )

    child = parent.derive_child(
        run_id="child-run",
        agent_id="parent-agent.child-1",
        trace_metadata={"child_goal": "inspect"},
    )

    assert child.session_id == "session-1"
    assert child.run_id == "child-run"
    assert child.agent_id == "parent-agent.child-1"
    assert child.parent_run_id == "parent-run"
    assert child.environment is environment
    assert child.context_budget is budget
    assert child.trace_metadata == {
        "request_id": "req-1",
        "child_goal": "inspect",
    }


def test_context_budget_rejects_bool_token_counts() -> None:
    """``bool`` is an ``int`` subclass — reject it explicitly."""
    with pytest.raises(TypeError, match="bool"):
        ContextBudget(max_input_tokens=cast(int, True))


def test_context_budget_rejects_reserved_exceeding_max_output() -> None:
    with pytest.raises(ValueError, match="reserved_output_tokens"):
        ContextBudget(reserved_output_tokens=2048, max_output_tokens=1024)


def test_agent_run_context_rejects_empty_child_run_id() -> None:
    parent = AgentRunContext(
        session_id="session-1",
        run_id="parent-run",
        agent_id="parent-agent",
        environment=StubEnvironment(),
    )

    with pytest.raises(ValueError, match="child run_id must be non-empty"):
        parent.derive_child(run_id="", agent_id="child-agent")
