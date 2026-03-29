import pytest
from pathlib import Path
from coding_agent.plugins.storage import StoragePlugin
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
