from typing import Any

import pytest
from agentkit.result.models import ArtifactRef
from agentkit.storage.protocols import ArtifactStore, TapeStore, DocIndex, SessionStore


class InMemoryTapeStore:
    """Minimal TapeStore for protocol testing."""

    def __init__(self):
        self._tapes: dict[str, list[dict[str, Any]]] = {}

    async def save(self, tape_id: str, entries: list[dict[str, Any]]) -> None:
        self._tapes[tape_id] = entries

    async def load(self, tape_id: str) -> list[dict[str, Any]]:
        return self._tapes.get(tape_id, [])

    async def list_ids(self) -> list[str]:
        return list(self._tapes.keys())

    async def truncate(self, tape_id: str, keep: int) -> None:
        if tape_id not in self._tapes:
            return
        self._tapes[tape_id] = self._tapes[tape_id][:keep]


class InMemoryDocIndex:
    def __init__(self):
        self._docs: list[dict[str, Any]] = []

    async def upsert(self, doc_id: str, text: str, metadata: dict[str, Any]) -> None:
        self._docs.append({"id": doc_id, "text": text, "metadata": metadata})

    async def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return self._docs[:limit]

    async def delete(self, doc_id: str) -> None:
        self._docs = [d for d in self._docs if d["id"] != doc_id]


class InMemorySessionStore:
    def __init__(self):
        self._sessions: dict[str, dict[str, Any]] = {}

    async def save_session(self, session_id: str, data: dict[str, Any]) -> None:
        self._sessions[session_id] = data

    async def load_session(self, session_id: str) -> dict[str, Any] | None:
        return self._sessions.get(session_id)

    async def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())

    async def delete_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


class InMemoryArtifactStore:
    def __init__(self):
        self._refs_by_id: dict[str, ArtifactRef] = {}
        self._session_index: dict[str, list[str]] = {}
        self._turn_index: dict[tuple[str, str], list[str]] = {}

    async def save_artifact_ref(
        self,
        session_id: str,
        artifact_ref: ArtifactRef,
        *,
        turn_id: str | None = None,
    ) -> None:
        self._refs_by_id[artifact_ref.artifact_id] = artifact_ref
        self._session_index.setdefault(session_id, []).append(artifact_ref.artifact_id)
        if turn_id is not None:
            key = (session_id, turn_id)
            self._turn_index.setdefault(key, []).append(artifact_ref.artifact_id)

    async def load_artifact_ref(self, artifact_id: str) -> ArtifactRef | None:
        return self._refs_by_id.get(artifact_id)

    async def list_artifact_refs(
        self,
        session_id: str,
        *,
        turn_id: str | None = None,
    ) -> list[ArtifactRef]:
        if turn_id is None:
            ids = self._session_index.get(session_id, [])
        else:
            ids = self._turn_index.get((session_id, turn_id), [])
        return [self._refs_by_id[artifact_id] for artifact_id in ids]

    async def delete_artifact_ref(self, artifact_id: str) -> None:
        self._refs_by_id.pop(artifact_id, None)
        for ids in self._session_index.values():
            if artifact_id in ids:
                ids.remove(artifact_id)
        for ids in self._turn_index.values():
            if artifact_id in ids:
                ids.remove(artifact_id)

    async def delete_artifact_refs_for_session(self, session_id: str) -> None:
        ids = self._session_index.pop(session_id, [])
        for artifact_id in ids:
            self._refs_by_id.pop(artifact_id, None)
        for key in list(self._turn_index):
            if key[0] == session_id:
                self._turn_index.pop(key)


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

    def test_artifact_store_satisfies_protocol(self):
        store = InMemoryArtifactStore()
        assert isinstance(store, ArtifactStore)

    @pytest.mark.asyncio
    async def test_artifact_store_protocol_saves_and_lists_refs(self):
        store = InMemoryArtifactStore()
        patch_ref = ArtifactRef(
            artifact_id="artifact_patch_1",
            kind="patch",
            title="Patch",
            uri="agentkit://artifacts/artifact_patch_1",
            metadata={"bytes": 128},
        )
        log_ref = ArtifactRef(
            artifact_id="artifact_log_1",
            kind="log",
            title="Log",
        )

        await store.save_artifact_ref("session_1", patch_ref, turn_id="turn_1")
        await store.save_artifact_ref("session_1", log_ref, turn_id="turn_2")

        assert await store.load_artifact_ref("artifact_patch_1") == patch_ref
        assert await store.list_artifact_refs("session_1") == [patch_ref, log_ref]
        assert await store.list_artifact_refs("session_1", turn_id="turn_1") == [
            patch_ref
        ]

    @pytest.mark.asyncio
    async def test_artifact_store_protocol_deletes_refs_for_session(self):
        store = InMemoryArtifactStore()
        patch_ref = ArtifactRef(artifact_id="artifact_patch_1", kind="patch")
        other_ref = ArtifactRef(artifact_id="artifact_patch_2", kind="patch")

        await store.save_artifact_ref("session_1", patch_ref, turn_id="turn_1")
        await store.save_artifact_ref("session_2", other_ref, turn_id="turn_1")
        await store.delete_artifact_refs_for_session("session_1")

        assert await store.load_artifact_ref("artifact_patch_1") is None
        assert await store.list_artifact_refs("session_1") == []
        assert await store.load_artifact_ref("artifact_patch_2") == other_ref
