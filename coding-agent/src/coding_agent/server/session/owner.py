"""Owner lease acquire/renew/release and fencing checks."""

from __future__ import annotations

import logging
from datetime import (
    UTC,
    datetime,
)
from coding_agent.server.stores.session_owner_store import OwnerAuthority
from coding_agent.server.stores.session_owner_store import SessionOwnerStoreProtocol
from coding_agent.server.stores.session_owner_store import SessionOwnershipConflictError
from coding_agent.server.stores.session_owner_store import (
    SessionOwnershipConflictReason,
)

logger = logging.getLogger("coding_agent.server.session_manager")


class OwnerOps:
    @property
    def owner_lease_seconds(self) -> float:
        return self._owner_lease_seconds

    @property
    def has_owner_leases_configured(self) -> bool:
        return self._owner_store is not None

    def configure_owner_leases(
        self,
        *,
        owner_store: SessionOwnerStoreProtocol | None,
        owner_id: str | None,
        fencing_token: int | None,
        owner_lease_seconds: float = 30.0,
    ) -> None:
        if owner_store is None and (owner_id is not None or fencing_token is not None):
            raise ValueError(
                "owner_store must be provided when owner_id or fencing_token is set"
            )
        if owner_store is not None and (owner_id is None or fencing_token is None):
            raise ValueError(
                "owner_id and fencing_token must be provided when owner_store is set"
            )
        if owner_lease_seconds <= 0:
            raise ValueError("owner_lease_seconds must be positive")
        self._owner_store = owner_store
        self._owner_id = owner_id
        self._fencing_token = fencing_token
        self._owner_lease_seconds = owner_lease_seconds
        self._owner_authorities: dict[str, OwnerAuthority] = {}
        self._configure_pg_durable_store_if_available()

    async def _assert_owner(self, session_id: str) -> None:
        if self._owner_store is None:
            return
        if self._owner_id is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

        owner = await self._owner_store.get_owner(session_id)
        if owner is None:
            raise SessionOwnershipConflictError(
                "session has no owner",
                reason=SessionOwnershipConflictReason.MISSING_OWNER,
            )
        if owner.lease_expires_at <= datetime.now(UTC):
            raise SessionOwnershipConflictError(
                "session owner lease expired",
                reason=SessionOwnershipConflictReason.EXPIRED_LEASE,
            )

        current_owner_id = owner.owner_id
        current_fencing_token = owner.fencing_token
        authority = self._owner_authorities.get(session_id)
        if authority is not None:
            if (
                current_owner_id != authority.owner_id
                or current_fencing_token != authority.epoch
            ):
                raise SessionOwnershipConflictError(
                    "stale owner or fencing token rejected"
                )
            return

        if self._fencing_token is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

        if (
            current_owner_id != self._owner_id
            or current_fencing_token != self._fencing_token
        ):
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

    def _owner_authority_for_session(self, session_id: str) -> OwnerAuthority:
        authority = self._owner_authorities.get(session_id)
        if authority is None:
            raise SessionOwnershipConflictError(
                "durable mutation requires owner authority"
            )
        return authority

    async def authorize_event_stream(self, session_id: str) -> None:
        await self._assert_owner(session_id)

    async def verify_event_stream_ownership(self, session_id: str) -> None:
        await self._assert_owner(session_id)

    async def _acquire_owner_for_session(self, session_id: str) -> None:
        if self._owner_store is None:
            return
        if self._owner_id is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        acquire_authority = getattr(self._owner_store, "acquire_authority", None)
        if callable(acquire_authority):
            authority = await acquire_authority(
                session_id,
                self._owner_id,
                lease_seconds=self._owner_lease_seconds,
            )
            if not isinstance(authority, OwnerAuthority):
                raise TypeError("acquire_authority must return OwnerAuthority")
            self._owner_authorities[session_id] = authority
            return
        if self._fencing_token is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        acquired = await self._owner_store.acquire(
            session_id,
            self._owner_id,
            lease_seconds=self._owner_lease_seconds,
            fencing_token=self._fencing_token,
        )
        if not acquired:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

    async def _holds_owner_lease(self, session_id: str) -> bool:
        if self._owner_store is None:
            return False
        if self._owner_id is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        owner = await self._owner_store.get_owner(session_id)
        if owner is None:
            return False
        authority = self._owner_authorities.get(session_id)
        if authority is not None:
            return (
                owner.owner_id == authority.owner_id
                and owner.fencing_token == authority.epoch
            )
        if self._fencing_token is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        return (
            owner.owner_id == self._owner_id
            and owner.fencing_token == self._fencing_token
        )

    async def _holds_active_owner_lease(self, session_id: str) -> bool:
        if self._owner_store is None:
            return False
        if self._owner_id is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        owner = await self._owner_store.get_owner(session_id)
        if owner is None:
            return False
        authority = self._owner_authorities.get(session_id)
        if authority is not None:
            return (
                owner.owner_id == authority.owner_id
                and owner.fencing_token == authority.epoch
                and owner.lease_expires_at > datetime.now(UTC)
            )
        if self._fencing_token is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        return (
            owner.owner_id == self._owner_id
            and owner.fencing_token == self._fencing_token
            and owner.lease_expires_at > datetime.now(UTC)
        )

    async def release_owned_sessions(self) -> None:
        if self._owner_store is None:
            return
        if self._owner_id is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        for session_id in await self.list_sessions_async():
            await self._release_owner_lease_for_session(session_id)

    async def _release_owner_lease_for_session(self, session_id: str) -> None:
        if self._owner_store is None:
            return
        if self._owner_id is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        if not await self._holds_owner_lease(session_id):
            return
        authority = self._owner_authorities.get(session_id)
        fencing_token = (
            authority.epoch if authority is not None else self._fencing_token
        )
        if fencing_token is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        try:
            released = await self._owner_store.release(
                session_id,
                self._owner_id,
                fencing_token,
            )
        except Exception:
            logger.warning(
                "Failed to release owner lease for session %s owned by %s with fencing token %s",
                session_id,
                self._owner_id,
                fencing_token,
                exc_info=True,
            )
            return
        if not released:
            logger.warning(
                "Failed to release owner lease for session %s owned by %s with fencing token %s",
                session_id,
                self._owner_id,
                fencing_token,
            )
            return
        self._owner_authorities.pop(session_id, None)

    async def renew_owner_leases(self) -> list[str]:
        lost_active_sessions: list[str] = []
        if self._owner_store is None:
            return lost_active_sessions
        if self._owner_id is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        now = datetime.now(UTC)
        for session_id in await self.list_sessions_async():
            owner = await self._owner_store.get_owner(session_id)
            if owner is None:
                if await self._cancel_active_turn_after_owner_loss(session_id):
                    lost_active_sessions.append(session_id)
                continue
            authority = self._owner_authorities.get(session_id)
            if authority is not None:
                owns_session = (
                    owner.owner_id == authority.owner_id
                    and owner.fencing_token == authority.epoch
                    and owner.lease_expires_at > now
                )
                log_token = authority.epoch
            else:
                if self._fencing_token is None:
                    raise SessionOwnershipConflictError(
                        "stale owner or fencing token rejected"
                    )
                owns_session = (
                    owner.owner_id == self._owner_id
                    and owner.fencing_token == self._fencing_token
                    and owner.lease_expires_at > now
                )
                log_token = self._fencing_token
            if not owns_session:
                if await self._cancel_active_turn_after_owner_loss(session_id):
                    lost_active_sessions.append(session_id)
                continue
            try:
                renew_authority = getattr(self._owner_store, "renew_authority", None)
                if authority is not None and callable(renew_authority):
                    renewed_authority = await renew_authority(
                        authority,
                        lease_seconds=self._owner_lease_seconds,
                    )
                    if not isinstance(renewed_authority, OwnerAuthority):
                        raise TypeError("renew_authority must return OwnerAuthority")
                    self._owner_authorities[session_id] = renewed_authority
                    renewed = True
                else:
                    if self._fencing_token is None:
                        raise SessionOwnershipConflictError(
                            "stale owner or fencing token rejected"
                        )
                    renewed = await self._owner_store.renew(
                        session_id,
                        self._owner_id,
                        lease_seconds=self._owner_lease_seconds,
                        new_fencing_token=self._fencing_token,
                        current_fencing_token=self._fencing_token,
                    )
            except Exception:
                logger.warning(
                    "Failed to renew owner lease for session %s owned by %s with fencing token %s",
                    session_id,
                    self._owner_id,
                    log_token,
                    exc_info=True,
                )
                continue
            if not renewed:
                logger.warning(
                    "Failed to renew owner lease for session %s owned by %s with fencing token %s",
                    session_id,
                    self._owner_id,
                    log_token,
                )
                if await self._cancel_active_turn_after_owner_loss(session_id):
                    lost_active_sessions.append(session_id)
        return lost_active_sessions

    async def _cancel_active_turn_after_owner_loss(self, session_id: str) -> bool:
        async with self._lock:
            session = self._session_cache.get(session_id)
            if session is None:
                return False
            if session.task is None or session.task.done():
                return False
            logger.warning(
                "Cancelling active turn for session %s after owner lease loss",
                session_id,
            )
            await self._runtime_cancel_orchestration.cancel(
                session,
                task=session.task,
            )
            return True

    async def backfill_owner_leases(self) -> None:
        if self._owner_store is None:
            return
        if self._owner_id is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

        now = datetime.now(UTC)
        for session_id in await self.list_sessions_async():
            try:
                owner = await self._owner_store.get_owner(session_id)
                if owner is not None and owner.lease_expires_at > now:
                    continue
                acquire_authority = getattr(
                    self._owner_store, "acquire_authority", None
                )
                if callable(acquire_authority):
                    authority = await acquire_authority(
                        session_id,
                        self._owner_id,
                        lease_seconds=self._owner_lease_seconds,
                    )
                    if not isinstance(authority, OwnerAuthority):
                        raise TypeError("acquire_authority must return OwnerAuthority")
                    self._owner_authorities[session_id] = authority
                    acquired = True
                    log_token = authority.epoch
                else:
                    if self._fencing_token is None:
                        raise SessionOwnershipConflictError(
                            "stale owner or fencing token rejected"
                        )
                    acquired = await self._owner_store.acquire(
                        session_id,
                        self._owner_id,
                        lease_seconds=self._owner_lease_seconds,
                        fencing_token=self._fencing_token,
                    )
                    log_token = self._fencing_token
            except Exception:
                logger.warning(
                    "Failed to backfill owner lease for session %s owned by %s with fencing token %s",
                    session_id,
                    self._owner_id,
                    self._fencing_token,
                    exc_info=True,
                )
                continue
            if not acquired:
                logger.warning(
                    "Failed to backfill owner lease for session %s owned by %s with fencing token %s",
                    session_id,
                    self._owner_id,
                    log_token,
                )

    async def acquire_session_owner(self, session_id: str) -> None:
        await self._acquire_owner_for_session(session_id)
