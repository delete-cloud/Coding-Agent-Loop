"""Runtime service wiring, ensure, and config replacement."""

from __future__ import annotations

import logging
from typing import (
    Any,
    cast,
)
from agentkit.environment import Environment
from agentkit.storage.pg import PGPool
from coding_agent.adapter import PipelineAdapter
from coding_agent.approval import ApprovalPolicy
from coding_agent.runs import (
    UNSET,
    UnsetType,
    LocalDaemonExecutorRef,
    RunTarget,
)
from coding_agent.server.session.models import Session

logger = logging.getLogger("coding_agent.server.session_manager")

_ACTIVE_RESUME_BLOCKING_RUN_STATUSES = {
    "queued",
    "requested",
    "claimed",
    "running",
    "cancelling",
}


class RuntimeOps:
    @property
    def pg_pool(self) -> PGPool:
        return self._get_pg_pool()

    def _resolve_environment(self, session: Session) -> Environment:
        return self._runtime_environment_resolver_service.resolve_environment_for_run_target(
            session.default_run_target
        )

    def _is_local_daemon_run_target(self, target: RunTarget | None) -> bool:
        if target is None:
            return False
        return isinstance(target.executor, LocalDaemonExecutorRef)

    async def _build_session_runtime(
        self,
        session: Session,
        *,
        model_name: str | None = None,
        provider_name: str | None = None,
        base_url: str | None | UnsetType = UNSET,
        max_steps: int | None = None,
        approval_policy: ApprovalPolicy | None = None,
    ) -> tuple[Any, Any, PipelineAdapter]:
        runtime = await self._local_daemon_runtime_preparation.build_runtime(
            session,
            model_name=model_name,
            provider_name=provider_name,
            base_url=base_url,
            max_steps=max_steps,
            approval_policy=approval_policy,
        )
        return (
            runtime.pipeline,
            runtime.ctx,
            cast(PipelineAdapter, runtime.adapter),
        )

    async def ensure_session_runtime(self, session_id: str) -> Any:
        return await self._runtime_ensure_orchestration.ensure_session_runtime(
            session_id
        )

    async def replace_session_runtime_config(
        self,
        session_id: str,
        *,
        model_name: str | None = None,
        provider_name: str | None = None,
        base_url: str | None | UnsetType = UNSET,
    ) -> Session:
        async def replace_admitted_runtime(session: object) -> Session:
            admitted_session = cast(Session, session)
            resolved_model = (
                model_name if model_name is not None else admitted_session.model_name
            )
            if not resolved_model:
                raise RuntimeError(
                    "session is missing model_name, cannot replace runtime"
                )
            return await self._runtime_replacement_service.replace_runtime_config(
                admitted_session,
                model_name=resolved_model,
                provider_name=provider_name,
                base_url=base_url,
                build_runtime=self._build_session_runtime,
                persist_session=self._persist_session_async,
            )

        return cast(
            Session,
            await self._runtime_maintenance_admission.run_exclusive(
                session_id,
                replace_admitted_runtime,
            ),
        )
