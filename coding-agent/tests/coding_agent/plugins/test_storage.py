# pyright: reportMissingTypeStubs=false, reportUnusedImport=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportPrivateLocalImportUsage=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportAny=false

import pytest
from pathlib import Path
import coding_agent.plugins.storage as storage_module
from agentkit.storage.pg import PGSessionStore
from coding_agent.plugins.storage import StoragePlugin
from coding_agent.plugins.storage import JSONLTapeStore
from agentkit.storage.protocols import TapeStore, SessionStore
from agentkit.tape.store import ForkTapeStore


class TestStoragePlugin:
    def test_state_key(self):
        plugin = StoragePlugin(data_dir=Path("/tmp/test-data"))
        assert plugin.state_key == "storage"

    def test_hooks_include_provide_storage(self):
        plugin = StoragePlugin(data_dir=Path("/tmp/test-data"))
        hooks = plugin.hooks()
        assert "provide_storage" in hooks

    def test_provide_storage_returns_fork_tape_store(self, tmp_path):
        plugin = StoragePlugin(data_dir=tmp_path)
        result = plugin.provide_storage()
        assert isinstance(result, ForkTapeStore)

    def test_mount_returns_initial_state(self, tmp_path):
        plugin = StoragePlugin(data_dir=tmp_path)
        hooks = plugin.hooks()
        state = hooks["mount"]()
        assert "session_store" in state
        assert isinstance(state["session_store"], SessionStore)

    def test_uses_agent_data_dir_env_when_data_dir_not_provided(
        self, tmp_path, monkeypatch
    ):
        env_data_dir = tmp_path / "agent-data"
        monkeypatch.setenv("AGENT_DATA_DIR", str(env_data_dir))

        plugin = StoragePlugin(data_dir=None)
        tape_store = plugin.provide_storage()
        state = plugin.hooks()["mount"]()

        assert isinstance(tape_store, ForkTapeStore)
        assert plugin._data_dir == env_data_dir
        assert (env_data_dir / "tapes").exists()
        assert (env_data_dir / "sessions").exists()
        assert isinstance(state["session_store"], SessionStore)

    def test_mount_uses_pg_session_store_when_configured(self, tmp_path, monkeypatch):
        constructed: list[tuple[str, str]] = []

        class StubPGPool:
            def __init__(self, *, dsn: str) -> None:
                self.dsn: str = dsn

        class StubPGSessionStore:
            def __init__(self, *, pool: StubPGPool) -> None:
                constructed.append((pool.dsn, type(pool).__name__))

            async def save_session(
                self, session_id: str, data: dict[str, object]
            ) -> None:
                raise NotImplementedError

            async def load_session(self, session_id: str) -> dict[str, object] | None:
                raise NotImplementedError

            async def list_sessions(self) -> list[str]:
                raise NotImplementedError

            async def delete_session(self, session_id: str) -> None:
                raise NotImplementedError

        monkeypatch.setattr(storage_module, "PGPool", StubPGPool)
        monkeypatch.setattr(storage_module, "PGSessionStore", StubPGSessionStore)

        plugin = StoragePlugin(
            data_dir=tmp_path,
            config={
                "session_backend": "pg",
                "dsn": "postgresql://sessions",
            },
        )

        state = plugin.hooks()["mount"]()

        assert isinstance(state["session_store"], SessionStore)
        assert constructed == [("postgresql://sessions", "StubPGPool")]

    def test_mount_requires_dsn_for_pg_backend(self, tmp_path):
        plugin = StoragePlugin(data_dir=tmp_path, config={"session_backend": "pg"})

        with pytest.raises(ValueError, match="storage.dsn is required"):
            plugin.hooks()["mount"]()

    def test_mount_surfaces_missing_asyncpg_dependency(self, tmp_path, monkeypatch):
        class RaisingPGPool:
            def __init__(self, *, dsn: str) -> None:
                _ = dsn
                raise ImportError("asyncpg is required for PostgreSQL storage backends")

        monkeypatch.setattr(storage_module, "PGPool", RaisingPGPool)

        plugin = StoragePlugin(
            data_dir=tmp_path,
            config={
                "session_backend": "pg",
                "dsn": "postgresql://sessions",
            },
        )

        with pytest.raises(ImportError, match="asyncpg is required"):
            plugin.hooks()["mount"]()

    @pytest.mark.asyncio
    async def test_on_shutdown_awaits_pg_pool_close(self, tmp_path, monkeypatch):
        close_calls: list[str] = []

        class StubPGPool:
            def __init__(self, *, dsn: str) -> None:
                self.dsn = dsn

            async def close(self) -> None:
                close_calls.append(self.dsn)

        class StubPGSessionStore:
            def __init__(self, *, pool: StubPGPool) -> None:
                self._pool = pool

            async def save_session(
                self, session_id: str, data: dict[str, object]
            ) -> None:
                raise NotImplementedError

            async def load_session(self, session_id: str) -> dict[str, object] | None:
                raise NotImplementedError

            async def list_sessions(self) -> list[str]:
                raise NotImplementedError

            async def delete_session(self, session_id: str) -> None:
                raise NotImplementedError

        monkeypatch.setattr(storage_module, "PGPool", StubPGPool)
        monkeypatch.setattr(storage_module, "PGSessionStore", StubPGSessionStore)

        plugin = StoragePlugin(
            data_dir=tmp_path,
            config={
                "session_backend": "pg",
                "dsn": "postgresql://sessions",
            },
        )
        plugin.hooks()["mount"]()

        await plugin.on_shutdown()

        assert close_calls == ["postgresql://sessions"]


class TestJSONLTapeStore:
    @pytest.mark.asyncio
    async def test_save_uses_executor_for_file_io(self, tmp_path, monkeypatch):
        store = JSONLTapeStore(tmp_path)
        recorded: list[tuple[object | None, object]] = []

        async def fake_run_in_executor(_self, executor, func):
            recorded.append((executor, func))
            return func()

        loop = type("LoopStub", (), {"run_in_executor": fake_run_in_executor})()

        monkeypatch.setattr(storage_module.asyncio, "get_running_loop", lambda: loop)

        await store.save("test-tape", [{"kind": "message", "payload": {"x": 1}}])

        assert len(recorded) == 1

    @pytest.mark.asyncio
    async def test_load_uses_executor_for_file_io(self, tmp_path, monkeypatch):
        store = JSONLTapeStore(tmp_path)
        path = tmp_path / "test-tape.jsonl"
        path.write_text('{"kind": "message", "payload": {"x": 1}}\n')

        recorded: list[tuple[object | None, object]] = []

        async def fake_run_in_executor(_self, executor, func):
            recorded.append((executor, func))
            return func()

        loop = type("LoopStub", (), {"run_in_executor": fake_run_in_executor})()

        monkeypatch.setattr(storage_module.asyncio, "get_running_loop", lambda: loop)

        entries = await store.load("test-tape")

        assert len(recorded) == 1
        assert entries == [{"kind": "message", "payload": {"x": 1}}]

    def test_memory_record_helpers_round_trip(self, tmp_path):
        store = JSONLTapeStore(tmp_path)

        store.append_memory_record(
            "session-1",
            {
                "summary": "Persisted topic memory",
                "tags": ["src/auth.py"],
                "importance": 0.8,
            },
        )

        assert store.load_memory_records("session-1") == [
            {
                "summary": "Persisted topic memory",
                "tags": ["src/auth.py"],
                "importance": 0.8,
            }
        ]
