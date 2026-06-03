from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from agentkit.runtime.context import AgentRunContext
from agentkit.tape.tape import Tape
from coding_agent.environment.local import LocalEnvironment
from coding_agent.environment.execution_binding import LocalExecutionBinding
from coding_agent.runs import RuntimeContextBindingService


@dataclass
class FakeSession:
    id: str = "session-1"
    execution_binding: LocalExecutionBinding = LocalExecutionBinding(
        workspace_root="/workspace"
    )


@dataclass
class FakeResumeContext:
    previous_run_id: str = "run-parent"

    def metadata(self) -> dict[str, Any]:
        return {"resume_from": self.previous_run_id, "resume_reason": "test"}


class FakeContext:
    def __init__(self) -> None:
        self.session_id = "stale-session"
        self.tape = Tape(tape_id="tape-1")
        self.config: dict[str, object] = {}
        self.run_context = AgentRunContext(
            session_id="stale-session",
            run_id="stale-run",
            agent_id=None,
            parent_run_id="old-parent",
            environment=LocalEnvironment(workspace_root="/workspace"),
            trace_metadata={"existing": "value"},
        )


def test_bind_root_run_identity_updates_context_and_trace_metadata() -> None:
    ctx = FakeContext()
    service = RuntimeContextBindingService(publish_subagent_message=lambda: None)

    service.bind_root_run_identity(FakeSession(), ctx, "run-1")

    assert ctx.session_id == "session-1"
    assert ctx.run_context.session_id == "session-1"
    assert ctx.run_context.run_id == "run-1"
    assert ctx.run_context.parent_run_id is None
    assert ctx.run_context.trace_metadata == {
        "existing": "value",
        "turn_id": "run-1",
        "tape_id": "tape-1",
        "execution_placement": "server_embedded",
        "execution_binding_kind": "local",
        "workspace_surface": "local_workspace",
        "execution_plane": "control_plane",
    }


def test_bind_root_run_identity_includes_resume_metadata() -> None:
    ctx = FakeContext()
    service = RuntimeContextBindingService(publish_subagent_message=lambda: None)

    service.bind_root_run_identity(
        FakeSession(),
        ctx,
        "run-2",
        resume_context=FakeResumeContext(),
    )

    assert ctx.run_context.parent_run_id == "run-parent"
    assert ctx.run_context.trace_metadata["resume_from"] == "run-parent"
    assert ctx.run_context.trace_metadata["resume_reason"] == "test"


def test_bind_root_run_identity_rejects_invalid_run_context() -> None:
    ctx = FakeContext()
    ctx.run_context = object()
    service = RuntimeContextBindingService(publish_subagent_message=lambda: None)

    with pytest.raises(TypeError, match="runtime context run_context"):
        service.bind_root_run_identity(FakeSession(), ctx, "run-1")


def test_bind_subagent_message_publisher_sets_config_value() -> None:
    publisher = object()
    ctx = FakeContext()
    service = RuntimeContextBindingService(publish_subagent_message=publisher)

    service.bind_subagent_message_publisher(ctx)

    assert ctx.config["subagent_message_publisher"] is publisher
