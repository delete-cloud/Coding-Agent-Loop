"""Storage Protocols — abstract interfaces for persistence."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TapeStore(Protocol):
    """Protocol for tape persistence."""

    async def save(self, tape_id: str, entries: list[dict[str, Any]]) -> None: ...
    async def load(self, tape_id: str) -> list[dict[str, Any]]: ...
    async def list_ids(self) -> list[str]: ...


@runtime_checkable
class DocIndex(Protocol):
    """Protocol for vector-searchable document storage."""

    async def upsert(
        self, doc_id: str, text: str, metadata: dict[str, Any]
    ) -> None: ...
    async def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]: ...
    async def delete(self, doc_id: str) -> None: ...


@runtime_checkable
class SessionStore(Protocol):
    """Protocol for session metadata persistence."""

    async def save_session(self, session_id: str, data: dict[str, Any]) -> None: ...
    async def load_session(self, session_id: str) -> dict[str, Any] | None: ...
    async def list_sessions(self) -> list[str]: ...
    async def delete_session(self, session_id: str) -> None: ...
