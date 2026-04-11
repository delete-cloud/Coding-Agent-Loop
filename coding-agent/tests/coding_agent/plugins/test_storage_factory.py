import pytest
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

from agentkit.storage.pg import PGPool


class TestStoragePluginFactory:
    def test_default_backend_is_jsonl(self, tmp_path: Path):
        from coding_agent.plugins.storage import StoragePlugin

        plugin = StoragePlugin(data_dir=tmp_path)

        assert plugin._backend == "jsonl"

    def test_jsonl_backend_creates_jsonl_store(self, tmp_path: Path):
        from coding_agent.plugins.storage import JSONLTapeStore, StoragePlugin

        plugin = StoragePlugin(data_dir=tmp_path, backend="jsonl")
        store = plugin._create_tape_store()

        assert isinstance(store, JSONLTapeStore)

    def test_pg_backend_without_pool_raises(self, tmp_path: Path):
        from coding_agent.plugins.storage import StoragePlugin

        plugin = StoragePlugin(data_dir=tmp_path, backend="pg")

        with pytest.raises(RuntimeError, match="pg_pool"):
            plugin._create_tape_store()

    def test_pg_backend_with_pool_creates_pg_store(self, tmp_path: Path):
        from coding_agent.plugins.storage import StoragePlugin

        mock_pg_pool = MagicMock()
        mock_pg_tape_store = MagicMock()
        mock_pg_session_lock = MagicMock()

        plugin = StoragePlugin(data_dir=tmp_path, backend="pg", pg_pool=mock_pg_pool)

        with patch(
            "coding_agent.plugins.storage._load_pg_types",
            return_value=(
                MagicMock(),
                mock_pg_session_lock,
                MagicMock(),
                mock_pg_tape_store,
            ),
        ):
            _ = plugin._create_tape_store()
            mock_pg_tape_store.assert_called_once_with(pool=mock_pg_pool)

    def test_pg_session_store_with_pool(self, tmp_path: Path):
        from coding_agent.plugins.storage import StoragePlugin

        mock_pg_pool = MagicMock()
        mock_pg_session_store = MagicMock()
        mock_pg_session_lock = MagicMock()

        plugin = StoragePlugin(data_dir=tmp_path, backend="pg", pg_pool=mock_pg_pool)

        with patch(
            "coding_agent.plugins.storage._load_pg_types",
            return_value=(
                MagicMock(),
                mock_pg_session_lock,
                mock_pg_session_store,
                MagicMock(),
            ),
        ):
            _ = plugin._create_session_store()
            mock_pg_session_store.assert_called_once_with(pool=mock_pg_pool)

    def test_jsonl_session_store(self, tmp_path: Path):
        from agentkit.storage.session import FileSessionStore
        from coding_agent.plugins.storage import StoragePlugin

        plugin = StoragePlugin(data_dir=tmp_path, backend="jsonl")
        store = plugin._create_session_store()

        assert isinstance(store, FileSessionStore)

    def test_provide_storage_uses_factory(self, tmp_path: Path):
        from agentkit.tape.store import ForkTapeStore
        from coding_agent.plugins.storage import StoragePlugin

        plugin = StoragePlugin(data_dir=tmp_path, backend="jsonl")
        storage = plugin.provide_storage()

        assert isinstance(storage, ForkTapeStore)

    def test_no_lock_when_jsonl_backend(self, tmp_path: Path):
        from coding_agent.plugins.storage import StoragePlugin

        plugin = StoragePlugin(data_dir=tmp_path, backend="jsonl")

        assert plugin.session_lock is None

    def test_pg_lock_stored_when_pg_backend(self, tmp_path: Path):
        from coding_agent.plugins.storage import StoragePlugin

        mock_pg_pool = MagicMock()

        plugin = StoragePlugin(data_dir=tmp_path, backend="pg", pg_pool=mock_pg_pool)

        assert plugin._session_lock is not None

    @pytest.mark.asyncio
    async def test_shutdown_closes_pg_pool_for_mixed_backend(self, tmp_path: Path):
        from coding_agent.plugins.storage import StoragePlugin

        class StubPGPool:
            def __init__(self) -> None:
                self.closed = False

            async def close(self) -> None:
                self.closed = True

            async def acquire(self):
                raise AssertionError("acquire should not be called in this test")

            async def release(self, connection):
                _ = connection
                raise AssertionError("release should not be called in this test")

        pg_pool_stub = StubPGPool()
        pg_pool = cast(PGPool, cast(object, pg_pool_stub))
        plugin = StoragePlugin(
            data_dir=tmp_path,
            backend="pg",
            pg_pool=pg_pool,
            config={"session_backend": "file"},
        )
        _ = plugin.hooks()["mount"]()

        await plugin.on_shutdown()

        assert pg_pool_stub.closed is True
