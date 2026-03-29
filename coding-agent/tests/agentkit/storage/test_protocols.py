import pytest
from agentkit.storage.protocols import TapeStore, DocIndex, SessionStore


class InMemoryTapeStore:
    """Minimal TapeStore for protocol testing."""

    def __init__(self):
        self._tapes = {}

    async def save(self, tape_id: str, entries: list[dict]) -> None:
        self._tapes[tape_id] = entries

    async def load(self, tape_id: str) -> list[dict]:
        return self._tapes.get(tape_id, [])

    async def list_ids(self) -> list[str]:
        return list(self._tapes.keys())


class InMemoryDocIndex:
    def __init__(self):
        self._docs = []

    async def upsert(self, doc_id: str, text: str, metadata: dict) -> None:
        self._docs.append({"id": doc_id, "text": text, "metadata": metadata})

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        return self._docs[:limit]

    async def delete(self, doc_id: str) -> None:
        self._docs = [d for d in self._docs if d["id"] != doc_id]


class InMemorySessionStore:
    def __init__(self):
        self._sessions = {}

    async def save_session(self, session_id: str, data: dict) -> None:
        self._sessions[session_id] = data

    async def load_session(self, session_id: str) -> dict | None:
        return self._sessions.get(session_id)

    async def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())

    async def delete_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


class TestStorageProtocols:
    def test_tape_store_satisfies_protocol(self):
        store = InMemoryTapeStore()
        assert isinstance(store, TapeStore)

    def test_doc_index_satisfies_protocol(self):
        idx = InMemoryDocIndex()
        assert isinstance(idx, DocIndex)

    def test_session_store_satisfies_protocol(self):
        store = InMemorySessionStore()
        assert isinstance(store, SessionStore)
