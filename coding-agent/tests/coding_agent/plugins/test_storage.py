import pytest
from pathlib import Path
import coding_agent.plugins.storage as storage_module
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

    @pytest.mark.asyncio
    async def test_truncate_uses_executor_for_file_io(self, tmp_path, monkeypatch):
        store = JSONLTapeStore(tmp_path)
        path = tmp_path / "test-tape.jsonl"
        path.write_text(
            '{"kind": "message", "payload": {"x": 1}}\n'
            '{"kind": "message", "payload": {"x": 2}}\n'
        )

        recorded: list[tuple[object | None, object]] = []

        async def fake_run_in_executor(_self, executor, func):
            recorded.append((executor, func))
            return func()

        loop = type("LoopStub", (), {"run_in_executor": fake_run_in_executor})()

        monkeypatch.setattr(storage_module.asyncio, "get_running_loop", lambda: loop)

        await store.truncate("test-tape", 1)

        assert len(recorded) == 1
        assert path.read_text() == '{"kind": "message", "payload": {"x": 1}}\n'

    @pytest.mark.asyncio
    async def test_truncate_keep_zero_clears_file(self, tmp_path):
        store = JSONLTapeStore(tmp_path)
        path = tmp_path / "test-tape.jsonl"
        path.write_text(
            '{"kind": "message", "payload": {"x": 1}}\n'
            '{"kind": "message", "payload": {"x": 2}}\n'
        )

        await store.truncate("test-tape", 0)

        assert path.read_text() == ""

    @pytest.mark.asyncio
    async def test_truncate_missing_tape_is_noop(self, tmp_path):
        store = JSONLTapeStore(tmp_path)

        await store.truncate("missing", 0)

        assert not (tmp_path / "missing.jsonl").exists()

    @pytest.mark.asyncio
    async def test_truncate_rejects_negative_keep(self, tmp_path):
        store = JSONLTapeStore(tmp_path)

        with pytest.raises(ValueError, match="keep must be >= 0"):
            await store.truncate("test-tape", -1)

    @pytest.mark.asyncio
    async def test_truncate_failure_before_replace_keeps_original_file(
        self, tmp_path, monkeypatch
    ):
        store = JSONLTapeStore(tmp_path)
        path = tmp_path / "test-tape.jsonl"
        original = (
            '{"kind": "message", "payload": {"x": 1}}\n'
            '{"kind": "message", "payload": {"x": 2}}\n'
        )
        path.write_text(original, encoding="utf-8")

        def fail_replace(src: str | Path, dst: str | Path) -> None:
            raise OSError("boom")

        monkeypatch.setattr(storage_module.os, "replace", fail_replace)

        with pytest.raises(OSError, match="boom"):
            await store.truncate("test-tape", 1)

        assert path.read_text(encoding="utf-8") == original
        assert list(tmp_path.glob("test-tape.jsonl.*.tmp")) == []
