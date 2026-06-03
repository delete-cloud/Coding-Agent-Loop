from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol

from agentkit.runtime.context import AgentRunContext

from .lifecycle import RuntimeRunResumeContext
from .target import (
    RunTarget,
    run_target_execution_placement,
    run_target_execution_plane,
    run_target_executor_kind,
    run_target_workspace_surface,
)


class RuntimeContextBindingSession(Protocol):
    id: str
    default_run_target: RunTarget


@dataclass(frozen=True)
class RuntimeContextBindingService:
    publish_subagent_message: Callable[..., Any]

    def bind_subagent_message_publisher(self, ctx: Any) -> None:
        ctx.config["subagent_message_publisher"] = self.publish_subagent_message

    def bind_root_run_identity(
        self,
        session: RuntimeContextBindingSession,
        ctx: Any,
        run_id: str,
        *,
        resume_context: RuntimeRunResumeContext | None = None,
    ) -> None:
        if hasattr(ctx, "session_id"):
            ctx.session_id = session.id
        run_context = getattr(ctx, "run_context", None)
        if run_context is None:
            return
        if not isinstance(run_context, AgentRunContext):
            raise TypeError("runtime context run_context must be AgentRunContext")
        trace_metadata = dict(run_context.trace_metadata)
        trace_metadata["turn_id"] = run_id
        trace_metadata["tape_id"] = ctx.tape.tape_id
        target = session.default_run_target
        trace_metadata["execution_placement"] = run_target_execution_placement(target)
        trace_metadata["executor_kind"] = run_target_executor_kind(target)
        trace_metadata["workspace_surface"] = run_target_workspace_surface(target)
        trace_metadata["execution_plane"] = run_target_execution_plane(target)
        if resume_context is not None:
            trace_metadata.update(resume_context.metadata())
        ctx.run_context = replace(
            run_context,
            session_id=session.id,
            run_id=run_id,
            parent_run_id=(
                None if resume_context is None else resume_context.previous_run_id
            ),
            trace_metadata=trace_metadata,
        )


__all__ = [
    "RuntimeContextBindingService",
    "RuntimeContextBindingSession",
]
