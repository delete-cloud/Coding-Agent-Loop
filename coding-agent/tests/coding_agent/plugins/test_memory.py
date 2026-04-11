import pytest
from unittest.mock import AsyncMock, MagicMock
from coding_agent.plugins.memory import MemoryPlugin
from agentkit.directive.types import MemoryRecord
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry


class TestMemoryPlugin:
    def test_state_key(self):
        plugin = MemoryPlugin()
        assert plugin.state_key == "memory"

    def test_hooks(self):
        plugin = MemoryPlugin()
        hooks = plugin.hooks()
        assert "build_context" in hooks  # Grounding mode
        assert "on_turn_end" in hooks  # finish_action
        assert "mount" in hooks

    def test_mount_returns_initial_state(self):
        plugin = MemoryPlugin()
        state = plugin.do_mount()
        assert "memories" in state
        assert isinstance(state["memories"], list)

    def test_build_context_returns_grounding_messages(self):
        plugin = MemoryPlugin()
        # Simulate having some memories
        plugin._memories = [
            {"summary": "User prefers Python", "importance": 0.9},
            {"summary": "Project uses pytest", "importance": 0.7},
        ]
        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "help me debug"})
        )
        result = plugin.build_context(tape=tape)
        assert isinstance(result, list)
        assert len(result) > 0
        # Grounding messages should be system role
        assert all(msg["role"] == "system" for msg in result)

    def test_on_turn_end_returns_memory_record_directive(self):
        plugin = MemoryPlugin()
        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "fix auth.py"})
        )
        tape.append(
            Entry(
                kind="message",
                payload={"role": "assistant", "content": "I fixed the bug in auth.py"},
            )
        )
        result = plugin.on_turn_end(tape=tape)
        assert isinstance(result, MemoryRecord)
        assert result.summary != ""

    def test_on_turn_end_with_empty_tape(self):
        plugin = MemoryPlugin()
        tape = Tape()
        result = plugin.on_turn_end(tape=tape)
        # With empty tape, should return a minimal record or None
        assert result is None or isinstance(result, MemoryRecord)

    def test_memory_importance_scoring(self):
        plugin = MemoryPlugin()
        # Simple heuristic: longer conversations = more important
        tape = Tape()
        for i in range(10):
            tape.append(
                Entry(kind="message", payload={"role": "user", "content": f"step {i}"})
            )
            tape.append(Entry(kind="tool_call", payload={"name": "bash_run"}))
        result = plugin.on_turn_end(tape=tape)
        assert isinstance(result, MemoryRecord)
        assert result.importance > 0.3  # Multi-step should score higher
