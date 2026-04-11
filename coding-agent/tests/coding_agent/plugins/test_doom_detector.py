from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from coding_agent.plugins.doom_detector import DoomDetectorPlugin


def _make_tool_call(name: str, arguments: dict[str, Any] | None = None) -> Entry:
    return Entry(
        kind="tool_call",
        payload={
            "id": "call_001",
            "name": name,
            "arguments": arguments or {},
            "role": "assistant",
        },
    )


def _make_tool_result(name: str, content: str = "ok") -> Entry:
    return Entry(
        kind="tool_result",
        payload={"name": name, "content": content, "role": "tool"},
    )


@dataclass
class FakePipelineContext:
    tape: Tape
    plugin_states: dict[str, Any] = field(default_factory=dict)


class TestDoomDetectorPlugin:
    def test_state_key(self) -> None:
        plugin = DoomDetectorPlugin()
        assert plugin.state_key == "doom_detector"

    def test_hooks_include_on_checkpoint(self) -> None:
        plugin = DoomDetectorPlugin()
        hooks = plugin.hooks()
        assert "on_checkpoint" in hooks

    def test_default_threshold_is_3(self) -> None:
        plugin = DoomDetectorPlugin()
        assert plugin._threshold == 3

    def test_3_identical_calls_triggers_doom(self) -> None:
        """3 consecutive identical tool calls → doom_detected=True"""
        plugin = DoomDetectorPlugin()
        tape = Tape()
        for _ in range(3):
            tape.append(_make_tool_call("file_read", {"path": "/foo/bar.py"}))
            tape.append(_make_tool_result("file_read"))

        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)

        assert ctx.plugin_states["doom_detector"]["doom_detected"] is True

    def test_uses_tape_snapshot_for_current_turn_calls(self) -> None:
        class SnapshotOnlyTape(Tape):
            def __iter__(self):
                raise AssertionError("snapshot path should be used")

        plugin = DoomDetectorPlugin()
        tape = SnapshotOnlyTape()
        for _ in range(3):
            tape.append(_make_tool_call("file_read", {"path": "/foo/bar.py"}))
            tape.append(_make_tool_result("file_read"))

        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)

        assert ctx.plugin_states["doom_detector"]["doom_detected"] is True

    def test_4_identical_calls_triggers_doom(self) -> None:
        """4 consecutive identical tool calls → doom_detected=True"""
        plugin = DoomDetectorPlugin()
        tape = Tape()
        for _ in range(4):
            tape.append(_make_tool_call("file_read", {"path": "/foo/bar.py"}))
            tape.append(_make_tool_result("file_read"))

        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)

        assert ctx.plugin_states["doom_detector"]["doom_detected"] is True

    def test_2_identical_calls_no_doom(self) -> None:
        """2 consecutive identical tool calls → doom_detected=False"""
        plugin = DoomDetectorPlugin()
        tape = Tape()
        for _ in range(2):
            tape.append(_make_tool_call("file_read", {"path": "/foo/bar.py"}))
            tape.append(_make_tool_result("file_read"))

        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)

        assert ctx.plugin_states["doom_detector"]["doom_detected"] is False

    def test_different_calls_no_doom(self) -> None:
        """Different tool calls don't trigger doom even with many calls"""
        plugin = DoomDetectorPlugin()
        tape = Tape()
        for i in range(5):
            tape.append(_make_tool_call("file_read", {"path": f"/file_{i}.py"}))
            tape.append(_make_tool_result("file_read"))

        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)

        assert ctx.plugin_states["doom_detector"]["doom_detected"] is False

    def test_different_tool_names_no_doom(self) -> None:
        """Different tool names interleaved don't trigger doom"""
        plugin = DoomDetectorPlugin()
        tape = Tape()
        tape.append(_make_tool_call("file_read", {"path": "/a.py"}))
        tape.append(_make_tool_result("file_read"))
        tape.append(_make_tool_call("file_write", {"path": "/a.py", "content": "x"}))
        tape.append(_make_tool_result("file_write"))
        tape.append(_make_tool_call("file_read", {"path": "/a.py"}))
        tape.append(_make_tool_result("file_read"))

        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)

        assert ctx.plugin_states["doom_detector"]["doom_detected"] is False

    def test_streak_broken_by_different_call(self) -> None:
        """2 identical, then 1 different, then 2 identical → no doom"""
        plugin = DoomDetectorPlugin()
        tape = Tape()
        for _ in range(2):
            tape.append(_make_tool_call("grep", {"pattern": "foo"}))
            tape.append(_make_tool_result("grep"))
        tape.append(_make_tool_call("file_read", {"path": "/x.py"}))
        tape.append(_make_tool_result("file_read"))
        for _ in range(2):
            tape.append(_make_tool_call("grep", {"pattern": "foo"}))
            tape.append(_make_tool_result("grep"))

        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)

        assert ctx.plugin_states["doom_detector"]["doom_detected"] is False

    def test_custom_threshold_5_not_triggered_at_4(self) -> None:
        """Threshold=5: 4 identical → no doom"""
        plugin = DoomDetectorPlugin(threshold=5)
        tape = Tape()
        for _ in range(4):
            tape.append(_make_tool_call("bash", {"command": "ls"}))
            tape.append(_make_tool_result("bash"))

        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)

        assert ctx.plugin_states["doom_detector"]["doom_detected"] is False

    def test_custom_threshold_5_triggered_at_5(self) -> None:
        """Threshold=5: 5 identical → doom_detected=True"""
        plugin = DoomDetectorPlugin(threshold=5)
        tape = Tape()
        for _ in range(5):
            tape.append(_make_tool_call("bash", {"command": "ls"}))
            tape.append(_make_tool_result("bash"))

        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)

        assert ctx.plugin_states["doom_detector"]["doom_detected"] is True

    def test_threshold_1_triggers_on_single_call(self) -> None:
        """Threshold=1: even 1 call triggers doom"""
        plugin = DoomDetectorPlugin(threshold=1)
        tape = Tape()
        tape.append(_make_tool_call("bash", {"command": "ls"}))
        tape.append(_make_tool_result("bash"))

        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)

        assert ctx.plugin_states["doom_detector"]["doom_detected"] is True

    def test_empty_tape_no_doom(self) -> None:
        """Empty tape → no doom"""
        plugin = DoomDetectorPlugin()
        ctx = FakePipelineContext(tape=Tape())
        plugin.on_checkpoint(ctx=ctx)

        assert ctx.plugin_states["doom_detector"]["doom_detected"] is False

    def test_no_tool_calls_in_tape(self) -> None:
        """Tape with only messages → no doom"""
        plugin = DoomDetectorPlugin()
        tape = Tape()
        for i in range(5):
            tape.append(
                Entry(kind="message", payload={"role": "user", "content": f"msg {i}"})
            )

        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)

        assert ctx.plugin_states["doom_detector"]["doom_detected"] is False

    def test_no_ctx_does_not_crash(self) -> None:
        """Calling on_checkpoint without ctx should not raise"""
        plugin = DoomDetectorPlugin()
        plugin.on_checkpoint()

    def test_doom_reason_included(self) -> None:
        """When doom detected, state includes doom_loop reason"""
        plugin = DoomDetectorPlugin()
        tape = Tape()
        for _ in range(3):
            tape.append(_make_tool_call("file_read", {"path": "/foo.py"}))
            tape.append(_make_tool_result("file_read"))

        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)

        state = ctx.plugin_states["doom_detector"]
        assert state["doom_detected"] is True
        assert "reason" in state
        assert "doom_loop" in state["reason"]

    def test_previous_turn_calls_do_not_trigger_doom_in_next_turn(self) -> None:
        plugin = DoomDetectorPlugin()
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "first"}))

        for _ in range(2):
            tape.append(_make_tool_call("file_read", {"path": "/foo.py"}))
            tape.append(_make_tool_result("file_read"))

        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)
        assert ctx.plugin_states["doom_detector"]["doom_detected"] is False

        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "second"})
        )
        tape.append(_make_tool_call("file_read", {"path": "/foo.py"}))
        tape.append(_make_tool_result("file_read"))

        plugin.on_checkpoint(ctx=ctx)

        assert ctx.plugin_states["doom_detector"]["doom_detected"] is False

    def test_doom_detected_emits_session_event(self) -> None:
        plugin = DoomDetectorPlugin()
        tape = Tape()
        for _ in range(3):
            tape.append(_make_tool_call("file_read", {"path": "/foo.py"}))
            tape.append(_make_tool_result("file_read"))

        runtime = MagicMock()
        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx, runtime=runtime)

        runtime.notify.assert_called_once()
        _, kwargs = runtime.notify.call_args
        assert kwargs["event_type"] == "doom_detected"
        assert "reason" in kwargs["payload"]
