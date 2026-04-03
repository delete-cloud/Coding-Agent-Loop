"""Tests for SkillsPlugin — skill activation timing and /skill off behaviour."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from coding_agent.plugins.skills import SkillsPlugin
from coding_agent.skills import Skill


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plugin_with_skill(
    tmp_path: Path, skill_name: str = "test-skill"
) -> SkillsPlugin:
    """Create a SkillsPlugin backed by a temp directory with one skill."""
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    skill_file = skill_dir / f"{skill_name}.md"
    skill_file.write_text(
        "---\n"
        f"name: {skill_name}\n"
        "description: A test skill\n"
        "inputs: []\n"
        "---\n"
        "You are a test skill prompt body.\n"
    )
    return SkillsPlugin(skills_dir=skill_dir)


class FakeCtx:
    """Minimal pipeline context stub."""

    def __init__(self):
        self.plugin_states: dict = {}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSkillActivationTiming:
    """P1-1: build_context must return skill prompt on the first call after request_skill."""

    def test_build_context_returns_prompt_after_request_skill(self, tmp_path: Path):
        """After request_skill, the very next build_context call must inject the skill prompt."""
        plugin = _make_plugin_with_skill(tmp_path)
        ctx = FakeCtx()

        # Set pending via request_skill
        result = plugin.request_skill(ctx, "test-skill")
        assert "activated" in result.lower() or "will be" in result.lower()

        # First build_context call should return the skill prompt
        messages = plugin.build_context()
        assert len(messages) == 1
        assert messages[0]["role"] == "system"
        assert "test skill prompt body" in messages[0]["content"].lower()

    def test_pending_skill_cleared_after_build_context(self, tmp_path: Path):
        """After build_context consumes the pending skill, _pending_skill_name should be None."""
        plugin = _make_plugin_with_skill(tmp_path)
        ctx = FakeCtx()

        plugin.request_skill(ctx, "test-skill")
        plugin.build_context()  # consumes pending

        # Pending should be cleared (but skill stays active)
        assert plugin._pending_skill_name is None
        # Skill is still active — subsequent build_context still returns prompt
        messages = plugin.build_context()
        assert len(messages) == 1

    def test_skill_off_clears_both_active_and_pending(self, tmp_path: Path):
        """deactivate() must clear both _active_skill AND _pending_skill_name."""
        plugin = _make_plugin_with_skill(tmp_path)
        ctx = FakeCtx()

        plugin.request_skill(ctx, "test-skill")
        # Before consuming, deactivate
        plugin.deactivate()

        assert plugin._active_skill is None
        assert plugin._pending_skill_name is None

        # build_context should return nothing
        messages = plugin.build_context()
        assert messages == []

    def test_on_checkpoint_does_not_activate_pending(self, tmp_path: Path):
        """on_checkpoint must NOT activate pending skill — only build_context() does."""
        plugin = _make_plugin_with_skill(tmp_path)
        ctx = FakeCtx()

        plugin.request_skill(ctx, "test-skill")
        assert plugin._pending_skill_name == "test-skill"

        plugin.on_checkpoint(ctx=ctx)

        assert plugin._active_skill is None
        assert plugin._pending_skill_name == "test-skill"
