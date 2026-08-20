"""Fenced PostgreSQL runtime writes."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast
from coding_agent.stores.runtime_store import (
    AgentInteractionRecord,
    AgentRunRecord,
    JSONObject,
    PGRuntimeStore,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
    _agent_run_from_row,
    _interaction_from_row,
    _message_snapshot_from_row,
    _runtime_event_from_row,
)
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SessionOwnershipConflictError,
)
from coding_agent.stores.pg_durable.helpers import (
    _required_owned_row,
    _required_row,
    _required_str,
)


class PgRuntimeMixin:
    async def create_agent_run(
        self,
        authority: OwnerAuthority,
        record: AgentRunRecord,
    ) -> AgentRunRecord:
        if record.session_id != authority.session_id:
            raise SessionOwnershipConflictError("run target belongs to another owner")
        if record.tape_id is None:
            raise SessionOwnershipConflictError("run target is not bound to a tape")

        async def body(connection: Any) -> AgentRunRecord:
            await self._require_owner(connection, authority)
            await self._require_stable_tape(connection, authority, record.tape_id)
            row = await connection.fetchrow(
                self._UPSERT_OWNED_RUN_SQL,
                record.run_id,
                record.session_id,
                record.tape_id,
                record.parent_run_id,
                record.agent_id,
                record.status,
                record.started_at,
                record.ended_at,
                record.metadata,
                record.result,
                record.error,
                record.superseded_by_checkpoint_id,
                record.superseded_at,
            )
            return _agent_run_from_row(
                _required_owned_row(row, "run target belongs to another owner")
            )

        return cast(AgentRunRecord, await self._with_transaction(body))

    async def update_agent_run(
        self,
        authority: OwnerAuthority,
        run_id: str,
        *,
        status: str,
        ended_at: datetime | None,
        metadata: JSONObject,
        result: JSONObject,
        error: str | None,
    ) -> AgentRunRecord:
        async def body(connection: Any) -> AgentRunRecord:
            await self._require_owner(connection, authority)
            await self._require_run_owner(connection, authority, run_id)
            row = await connection.fetchrow(
                PGRuntimeStore._UPDATE_RUN_SQL,
                run_id,
                status,
                ended_at,
                metadata,
                result,
                error,
            )
            return _agent_run_from_row(_required_row(row, "agent run update"))

        return cast(AgentRunRecord, await self._with_transaction(body))

    async def append_runtime_event(
        self,
        authority: OwnerAuthority,
        record: RuntimeEventRecord,
    ) -> RuntimeEventRecord:
        async def body(connection: Any) -> RuntimeEventRecord:
            await self._require_owner(connection, authority)
            await self._require_run_owner(connection, authority, record.run_id)
            row = await connection.fetchrow(
                self._INSERT_OWNED_EVENT_SQL,
                record.event_id,
                record.run_id,
                record.event_kind,
                record.payload,
                record.created_at,
            )
            return _runtime_event_from_row(
                _required_owned_row(
                    row,
                    "runtime event target belongs to another owner",
                )
            )

        return cast(RuntimeEventRecord, await self._with_transaction(body))

    async def save_message_snapshot(
        self,
        authority: OwnerAuthority,
        record: RunMessageSnapshotRecord,
    ) -> RunMessageSnapshotRecord:
        async def body(connection: Any) -> RunMessageSnapshotRecord:
            await self._require_owner(connection, authority)
            await self._require_run_owner(connection, authority, record.run_id)
            row = await connection.fetchrow(
                self._UPSERT_OWNED_MESSAGE_SNAPSHOT_SQL,
                record.snapshot_id,
                record.run_id,
                record.messages,
                record.metadata,
                record.created_at,
            )
            return _message_snapshot_from_row(
                _required_owned_row(
                    row,
                    "message snapshot target belongs to another owner",
                )
            )

        return cast(RunMessageSnapshotRecord, await self._with_transaction(body))

    async def create_agent_interaction(
        self,
        authority: OwnerAuthority,
        record: AgentInteractionRecord,
    ) -> AgentInteractionRecord:
        async def body(connection: Any) -> AgentInteractionRecord:
            await self._require_owner(connection, authority)
            await self._require_run_owner(connection, authority, record.run_id)
            row = await connection.fetchrow(
                self._INSERT_OWNED_INTERACTION_SQL,
                record.interaction_id,
                record.run_id,
                record.interaction_kind,
                record.status,
                record.request_payload,
                record.response_payload,
                record.metadata,
                record.created_at,
                record.resolved_at,
            )
            return _interaction_from_row(
                _required_owned_row(row, "interaction target belongs to another owner")
            )

        return cast(AgentInteractionRecord, await self._with_transaction(body))

    async def resolve_agent_interaction(
        self,
        authority: OwnerAuthority,
        interaction_id: str,
        *,
        status: str,
        response_payload: JSONObject,
        resolved_at: datetime,
    ) -> AgentInteractionRecord:
        async def body(connection: Any) -> AgentInteractionRecord:
            await self._require_owner(connection, authority)
            existing = await connection.fetchrow(
                self._SELECT_INTERACTION_RUN_FOR_UPDATE_SQL,
                interaction_id,
            )
            if existing is None:
                raise KeyError(f"agent interaction not found: {interaction_id}")
            await self._require_run_owner(
                connection,
                authority,
                _required_str(dict(existing), "run_id"),
            )
            row = await connection.fetchrow(
                PGRuntimeStore._RESOLVE_INTERACTION_SQL,
                interaction_id,
                status,
                response_payload,
                resolved_at,
            )
            return _interaction_from_row(
                _required_row(row, "agent interaction resolve")
            )

        return cast(AgentInteractionRecord, await self._with_transaction(body))

    async def claim_attached_executor_run(
        self,
        authorities: Mapping[str, OwnerAuthority],
        *,
        session_id: str | None,
        executor_kind: str,
        claim_metadata: JSONObject,
    ) -> AgentRunRecord | None:
        if session_id is not None:
            authority = authorities.get(session_id)
            if authority is None:
                return None
            return await self._claim_attached_executor_run_for_authority(
                authority,
                executor_kind=executor_kind,
                claim_metadata=claim_metadata,
            )
        for authority in sorted(authorities.values(), key=lambda item: item.session_id):
            claimed = await self._claim_attached_executor_run_for_authority(
                authority,
                executor_kind=executor_kind,
                claim_metadata=claim_metadata,
            )
            if claimed is not None:
                return claimed
        return None

    async def _claim_attached_executor_run_for_authority(
        self,
        authority: OwnerAuthority,
        *,
        executor_kind: str,
        claim_metadata: JSONObject,
    ) -> AgentRunRecord | None:
        async def body(connection: Any) -> AgentRunRecord | None:
            await self._require_owner(connection, authority)
            row = await connection.fetchrow(
                PGRuntimeStore._CLAIM_ATTACHED_EXECUTOR_RUN_SQL,
                authority.session_id,
                executor_kind,
                claim_metadata,
            )
            if row is None:
                return None
            return _agent_run_from_row(row)

        return cast(AgentRunRecord | None, await self._with_transaction(body))
