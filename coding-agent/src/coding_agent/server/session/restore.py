"""Session resume plus tape/checkpoint restore."""

from __future__ import annotations

import logging
from typing import (
    Any,
    cast,
)
from agentkit.checkpoint.models import CheckpointMeta
from agentkit.tape.models import Anchor
from agentkit.tape.tape import Tape
from agentkit.runtime.contracts import OperationStateVersion
from coding_agent.stores.runtime_store import AgentRunRecord
from coding_agent.runs import (
    RemoteLoopOwnershipRetired,
    RuntimeResumeContext as SessionResumeContext,
)
from coding_agent.wire.consumer import LocalWireConsumer
from coding_agent.wire.local import LocalWire
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
)
from coding_agent.server.session.models import Session

logger = logging.getLogger("coding_agent.server.session_manager")


class RestoreOps:
    async def resume_session(
        self,
        session_id: str,
        *,
        prompt: str | None = None,
        resume_reason: str = "user_resume",
        previous_run_id: str | None = None,
        run_id_override: str | None = None,
    ) -> AgentRunRecord:
        return await self._runtime_resume_session_orchestration.resume_session(
            session_id,
            prompt=prompt,
            resume_reason=resume_reason,
            previous_run_id=previous_run_id,
            run_id_override=run_id_override,
        )

    async def _run_resumed_local_session(
        self,
        session_id: str,
        prompt: str,
        run_id: str,
        resume_context: SessionResumeContext,
    ) -> AgentRunRecord | None:
        await self.run_agent(
            session_id,
            prompt,
            run_id_override=run_id,
            resume_context=resume_context,
        )
        return None

    async def _request_resumed_attached_executor_run(
        self,
        session_id: str,
        prompt: str,
        run_id: str,
        resume_context: SessionResumeContext,
    ) -> AgentRunRecord:
        del session_id, prompt, run_id, resume_context
        raise RemoteLoopOwnershipRetired()

    def _append_live_resume_boundary_anchor(
        self, session: Session, anchor: Anchor
    ) -> None:
        runtime_ctx = session.runtime_ctx
        tape = getattr(runtime_ctx, "tape", None)
        if isinstance(tape, Tape) and tape.tape_id == session.tape_id:
            tape.append(anchor)

    async def _restore_tape(self, tape_id: str | None) -> Tape | None:
        if tape_id is None:
            return None
        entries = await self._tape_store.load(tape_id)
        if not entries:
            return Tape(tape_id=tape_id)
        return Tape.from_list(entries, tape_id=tape_id)

    def _make_restore_consumer(self, wire: LocalWire) -> LocalWireConsumer:
        async def _reject_approval(req: ApprovalRequest) -> ApprovalResponse:
            return ApprovalResponse(
                session_id=req.session_id,
                request_id=req.request_id,
                approved=False,
                feedback="Checkpoint restore does not support approval prompts",
            )

        return LocalWireConsumer(wire, _reject_approval)

    async def _restore_checkpoint(self, session: Session, checkpoint_id: str) -> None:
        await self._runtime_checkpoint_restore_service.restore(session, checkpoint_id)
        latest_active_run = (
            None
            if self._runtime_store is None
            else await self._runtime_control_services.queries().latest_runtime_run(
                session.id
            )
        )
        session.current_turn_id = (
            None if latest_active_run is None else latest_active_run.run_id
        )

    async def _restore_checkpoint_durable_state(
        self,
        session: Any,
        snapshot: Any,
    ) -> None:
        typed_session = cast(Session, session)
        active_runs = (
            []
            if self._runtime_store is None
            else await self._runtime_control_services.queries().list_active_runtime_runs(
                typed_session.id
            )
        )
        runs_at_checkpoint = [
            run for run in active_runs if run.started_at <= snapshot.meta.created_at
        ]
        latest_run = (
            None
            if not runs_at_checkpoint
            else max(runs_at_checkpoint, key=lambda run: (run.started_at, run.run_id))
        )
        typed_session.current_turn_id = (
            None if latest_run is None else latest_run.run_id
        )
        if self._local_durable_store is None and self._pg_durable_store is None:
            if typed_session.tape_id is None:
                raise ValueError("session has no stable tape id")
            await self._tape_store.truncate(
                typed_session.tape_id,
                snapshot.meta.entry_count,
            )
            await self._persist_session_async(typed_session)
            checkpoints = await self._checkpoint_service.list(typed_session.tape_id)
            for checkpoint_meta in checkpoints:
                if checkpoint_meta.entry_count > snapshot.meta.entry_count:
                    await self._checkpoint_service.delete(checkpoint_meta.checkpoint_id)
            return
        authority = self._owner_authority_for_session(typed_session.id)
        payload = cast(dict[str, Any], typed_session.to_store_data())
        if self._local_durable_store is not None:
            await self._local_durable_store.restore_checkpoint_state(
                authority,
                snapshot,
                payload,
            )
            return
        if self._pg_durable_store is None:
            raise RuntimeError("durable checkpoint restore store is not configured")
        await self._pg_durable_store.restore_checkpoint_state(
            authority,
            snapshot,
            payload,
        )

    async def _load_restore_point_operation_state(
        self,
        session_id: str,
        run_id: str,
    ) -> OperationStateVersion | None:
        store = self._authoritative_store()
        if store is None:
            return None
        return await store.load_operation_state(session_id, run_id)

    async def capture_checkpoint(
        self,
        session_id: str,
        *,
        label: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> CheckpointMeta:
        async def capture_admitted_checkpoint(session: object) -> CheckpointMeta:
            return await self._runtime_checkpoint_capture_service.capture(
                cast(Session, session),
                label=label,
                extra=extra,
            )

        return cast(
            CheckpointMeta,
            await self._runtime_maintenance_admission.run_exclusive(
                session_id,
                capture_admitted_checkpoint,
            ),
        )

    async def list_checkpoints(self, session_id: str) -> list[CheckpointMeta]:
        session = await self.get_session_async(session_id)
        return await self._runtime_checkpoint_query_service.list_checkpoints(session)

    async def restore_checkpoint(self, session_id: str, checkpoint_id: str) -> None:
        await self._runtime_checkpoint_restore_orchestration.restore_checkpoint(
            session_id,
            checkpoint_id,
        )
