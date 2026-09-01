"""Schema, transactions, session writes, and fencing helpers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from agentkit.storage.pg import (
    PGCheckpointStore,
    PGPool,
    PGSessionOwnerStore,
    PGTapeStore,
)
from coding_agent.topics.store import (
    PGTopicStore,
)
from coding_agent.stores.runtime_store import (
    PGRuntimeStore,
)
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SessionOwnershipConflictError,
    SessionOwnershipConflictReason,
)
from coding_agent.server.stores.session_store import PGSessionMetadataStore
from coding_agent.stores.pg_durable.helpers import (
    _require_payload_session,
    _required_dict,
    _required_str,
)
from coding_agent.runtime_activation import (
    RuntimeActivationState,
    stamp_session_payload_for_save,
)


class PgCoreMixin:
    def __init__(self, *, pool: PGPool) -> None:
        self._pool = pool
        self._schema_ready = False

    async def _ensure_schema(self) -> None:
        pool = await self._pool.get_pool()
        if self._schema_ready:
            return
        _ = await pool.execute(PGSessionOwnerStore._CREATE_TABLE_SQL)
        _ = await pool.execute(PGSessionMetadataStore._CREATE_TABLE_SQL)
        _ = await pool.execute(self._CREATE_SESSION_TAPES_SQL)
        _ = await pool.execute(PGTapeStore._CREATE_TABLE_SQL)
        _ = await pool.execute(PGCheckpointStore._CREATE_TABLE_SQL)
        _ = await pool.execute(PGRuntimeStore._CREATE_SCHEMA_SQL)
        _ = await pool.execute(PGTopicStore._CREATE_SCHEMA_SQL)
        _ = await pool.execute(self._CREATE_HARNESS_FACT_SOURCE_SQL)
        _ = await pool.execute(self._MIGRATE_HARNESS_FACT_SOURCE_SQL)
        _ = await pool.execute(self._CREATE_RUNTIME_ACTIVATION_SQL)
        self._schema_ready = True

    async def _with_transaction(self, body: Callable[[Any], Any]) -> Any:
        await self._ensure_schema()
        connection = await self._pool.acquire()
        try:
            _ = await connection.execute("BEGIN")
            result = await body(connection)
            _ = await connection.execute("COMMIT")
            return result
        except BaseException:
            try:
                _ = await connection.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            await self._pool.release(connection)

    async def session_id_for_tape(self, tape_id: str) -> str | None:
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        row = await pool.fetchrow(self._SELECT_SESSION_BY_TAPE_SQL, tape_id)
        if row is None:
            return None
        return _required_str(dict(row), "session_id")

    async def session_id_for_topic(self, topic_id: str) -> str | None:
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        row = await pool.fetchrow(self._SELECT_TOPIC_SESSION_TAPE_SQL, topic_id)
        if row is None:
            return None
        return _required_str(dict(row), "session_id")

    async def load_runtime_activation(self) -> RuntimeActivationState:
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        row = await pool.fetchrow(
            """
            SELECT new_sessions_enabled FROM runtime_activation
            WHERE singleton = 1
            """
        )
        enabled = False if row is None else bool(row["new_sessions_enabled"])
        return RuntimeActivationState(new_sessions_enabled=enabled)

    async def set_new_session_runtime_activation(
        self,
        *,
        enabled: bool,
    ) -> RuntimeActivationState:
        async def body(connection: Any) -> None:
            await connection.execute(
                """
                INSERT INTO runtime_activation (singleton, new_sessions_enabled)
                VALUES (1, $1)
                ON CONFLICT (singleton) DO UPDATE SET
                    new_sessions_enabled = EXCLUDED.new_sessions_enabled
                """,
                enabled,
            )

        await self._with_transaction(body)
        return RuntimeActivationState(new_sessions_enabled=enabled)

    async def load_session_payload(self, session_id: str) -> dict[str, Any] | None:
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        row = await pool.fetchrow(
            "SELECT payload FROM agent_http_sessions WHERE session_id = $1",
            session_id,
        )
        if row is None:
            return None
        payload = row["payload"]
        if not isinstance(payload, dict):
            raise TypeError("postgres session payload must be a JSON object")
        return payload

    async def save_session(
        self,
        authority: OwnerAuthority,
        payload: dict[str, Any],
    ) -> None:
        _require_payload_session(authority, payload)
        tape_id = payload.get("tape_id")
        if tape_id is not None and not isinstance(tape_id, str):
            raise TypeError("session payload tape_id must be a string")

        async def body(connection: Any) -> None:
            await self._require_owner(connection, authority)
            stored_row = await connection.fetchrow(
                self._SELECT_SESSION_FOR_UPDATE_SQL,
                authority.session_id,
            )
            stored = None if stored_row is None else stored_row["payload"]
            if stored is not None and not isinstance(stored, dict):
                raise TypeError("postgres session payload must be a JSON object")
            activation_row = await connection.fetchrow(
                """
                SELECT new_sessions_enabled FROM runtime_activation
                WHERE singleton = 1
                """
            )
            activation = RuntimeActivationState(
                new_sessions_enabled=(
                    False
                    if activation_row is None
                    else bool(activation_row["new_sessions_enabled"])
                )
            )
            stamped = stamp_session_payload_for_save(
                incoming=payload,
                stored=stored,
                activation=activation,
            )
            if tape_id:
                await self._bind_tape(connection, authority.session_id, tape_id)
            await connection.execute(
                self._UPSERT_SESSION_SQL,
                authority.session_id,
                stamped,
            )

        await self._with_transaction(body)

    async def delete_session(self, authority: OwnerAuthority) -> None:
        async def body(connection: Any) -> None:
            await self._require_owner(connection, authority)
            _ = await connection.fetchrow(
                self._SELECT_SESSION_FOR_UPDATE_SQL,
                authority.session_id,
            )
            await connection.execute(self._DELETE_SESSION_SQL, authority.session_id)
            await connection.execute(
                self._DELETE_SESSION_TAPE_SQL, authority.session_id
            )

        await self._with_transaction(body)

    async def _require_owner(self, connection: Any, authority: OwnerAuthority) -> None:
        row = await connection.fetchrow(
            self._SELECT_OWNER_FOR_UPDATE_SQL,
            authority.session_id,
        )
        if row is None:
            raise SessionOwnershipConflictError(
                "session owner lease is missing",
                reason=SessionOwnershipConflictReason.MISSING_OWNER,
            )
        row_dict = dict(row)
        lease_expires_at = row_dict.get("lease_expires_at")
        if not isinstance(lease_expires_at, datetime):
            raise TypeError("postgres owner row missing datetime lease_expires_at")
        now = datetime.now(lease_expires_at.tzinfo)
        if lease_expires_at <= now:
            raise SessionOwnershipConflictError(
                "session owner lease has expired",
                reason=SessionOwnershipConflictReason.EXPIRED_LEASE,
            )
        if (
            row_dict.get("owner_id") != authority.owner_id
            or row_dict.get("fencing_token") != authority.epoch
        ):
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

    async def _require_stable_tape(
        self,
        connection: Any,
        authority: OwnerAuthority,
        tape_id: str,
    ) -> None:
        row = await connection.fetchrow(
            self._SELECT_TAPE_SESSION_FOR_UPDATE_SQL,
            tape_id,
        )
        if row is None:
            raise SessionOwnershipConflictError("tape target is not bound to a session")
        if dict(row).get("session_id") != authority.session_id:
            raise SessionOwnershipConflictError("tape target belongs to another owner")

    async def _bind_tape(
        self,
        connection: Any,
        session_id: str,
        tape_id: str,
    ) -> None:
        await connection.execute(self._UPSERT_SESSION_TAPE_SQL, session_id, tape_id)
        session_row = await connection.fetchrow(
            self._SELECT_SESSION_TAPE_FOR_UPDATE_SQL,
            session_id,
        )
        if session_row is None:
            tape_row = await connection.fetchrow(
                self._SELECT_TAPE_SESSION_FOR_UPDATE_SQL,
                tape_id,
            )
            if tape_row is not None and dict(tape_row).get("session_id") != session_id:
                raise SessionOwnershipConflictError(
                    "tape target belongs to another session"
                )
            raise SessionOwnershipConflictError("session tape target is not bound")
        if dict(session_row).get("tape_id") != tape_id:
            raise SessionOwnershipConflictError("session tape target cannot be rebound")
        tape_row = await connection.fetchrow(
            self._SELECT_TAPE_SESSION_FOR_UPDATE_SQL,
            tape_id,
        )
        if tape_row is None:
            raise SessionOwnershipConflictError("tape target is not bound to a session")
        if dict(tape_row).get("session_id") != session_id:
            raise SessionOwnershipConflictError(
                "tape target belongs to another session"
            )

    async def _session_id_for_run(
        self,
        connection: Any,
        run_id: str,
        *,
        for_update: bool,
    ) -> str | None:
        del for_update
        row = await connection.fetchrow(self._SELECT_RUN_SESSION_FOR_UPDATE_SQL, run_id)
        if row is None:
            return None
        return _required_str(dict(row), "session_id")

    async def _require_run_owner(
        self,
        connection: Any,
        authority: OwnerAuthority,
        run_id: str,
    ) -> None:
        session_id = await self._session_id_for_run(
            connection,
            run_id,
            for_update=True,
        )
        if session_id is None:
            raise KeyError(f"agent run not found: {run_id}")
        if session_id != authority.session_id:
            raise SessionOwnershipConflictError("run target belongs to another owner")

    async def _require_checkpoint_owner(
        self,
        connection: Any,
        authority: OwnerAuthority,
        checkpoint_id: str,
    ) -> None:
        row = await connection.fetchrow(
            self._SELECT_CHECKPOINT_META_FOR_UPDATE_SQL,
            checkpoint_id,
        )
        if row is None:
            raise KeyError(f"checkpoint not found: {checkpoint_id}")
        meta = _required_dict(dict(row), "meta")
        if meta.get("session_id") != authority.session_id:
            raise SessionOwnershipConflictError(
                "checkpoint target belongs to another owner"
            )
