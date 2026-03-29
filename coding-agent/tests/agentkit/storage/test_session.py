import pytest
from pathlib import Path
from agentkit.storage.session import FileSessionStore
from agentkit.storage.protocols import SessionStore


class TestFileSessionStore:
    @pytest.fixture
    def store(self, tmp_path):
        return FileSessionStore(base_dir=tmp_path)

    def test_satisfies_protocol(self, store):
        assert isinstance(store, SessionStore)

    @pytest.mark.asyncio
    async def test_save_and_load(self, store):
        await store.save_session("ses-1", {"model": "gpt-4", "turns": 5})
        data = await store.load_session("ses-1")
        assert data is not None
        assert data["model"] == "gpt-4"

    @pytest.mark.asyncio
    async def test_load_missing_returns_none(self, store):
        data = await store.load_session("nonexistent")
        assert data is None

    @pytest.mark.asyncio
    async def test_list_sessions(self, store):
        await store.save_session("a", {"x": 1})
        await store.save_session("b", {"x": 2})
        ids = await store.list_sessions()
        assert set(ids) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_delete_session(self, store):
        await store.save_session("del-me", {"x": 1})
        await store.delete_session("del-me")
        assert await store.load_session("del-me") is None
