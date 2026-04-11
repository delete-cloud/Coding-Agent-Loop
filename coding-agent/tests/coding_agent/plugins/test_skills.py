"""Tests for SkillsPlugin — summary mode, discovery, activation timing, public API."""

from pathlib import Path
from typing import Any

import pytest

from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from agentkit.plugin.registry import PluginRegistry
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.runtime.hookspecs import HOOK_SPECS
from coding_agent.plugins.doom_detector import DoomDetectorPlugin
from coding_agent.plugins.skills import SkillsPlugin
from coding_agent.skills import SkillMetadata


def _make_plugin(
    tmp_path: Path,
    skills: dict[str, str] | None = None,
    extra_dirs: list[str] | None = None,
) -> SkillsPlugin:
    agents_dir = tmp_path / ".agents" / "skills"
    agents_dir.mkdir(parents=True, exist_ok=True)
    if skills:
        for name, desc in skills.items():
            d = agents_dir / name
            d.mkdir()
            (d / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {desc}\ninputs: []\n---\n"
                f"You are the {name} skill prompt body.\n"
            )
    no_global = tmp_path / "no-global"
    return SkillsPlugin(
        workspace_root=tmp_path,
        extra_dirs=extra_dirs or [],
        global_skills_dir=no_global,
    )


class FakeCtx:
    def __init__(self) -> None:
        self.plugin_states: dict[str, Any] = {}


class FakePipelineContext:
    def __init__(self, tape: Tape) -> None:
        self.tape = tape
        self.plugin_states: dict[str, Any] = {}


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


class TestSummaryMode:
    def test_build_context_includes_available_skills_when_skills_exist(
        self, tmp_path: Path
    ):
        plugin = _make_plugin(tmp_path, {"test-skill": "A test"})
        messages = plugin.build_context()
        assert len(messages) >= 1
        content = messages[0]["content"]
        assert "<available_skills>" in content
        assert "test-skill" in content
        assert "A test" in content

    def test_build_context_returns_empty_when_no_skills(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path)
        messages = plugin.build_context()
        assert messages == []

    def test_summary_xml_contains_skill_name_and_description(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path, {"alpha": "Alpha desc", "beta": "Beta desc"})
        messages = plugin.build_context()
        content = messages[0]["content"]
        assert "<name>alpha</name>" in content
        assert "<description>Alpha desc</description>" in content
        assert "<name>beta</name>" in content
        assert "<description>Beta desc</description>" in content

    def test_summary_xml_excludes_skill_body(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path, {"test-skill": "Desc"})
        messages = plugin.build_context()
        content = messages[0]["content"]
        assert "skill prompt body" not in content

    def test_active_skill_body_injected_alongside_summary(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path, {"active-skill": "Active test"})
        plugin._handle_skill_invoke({"name": "active-skill"})
        messages = plugin.build_context()
        assert len(messages) == 2
        assert "<available_skills>" in messages[0]["content"]
        assert "active-skill" in messages[0]["content"]
        assert "active-skill skill prompt body" in messages[1]["content"]


class TestNewDiscovery:
    def test_plugin_discovers_from_workspace(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path, {"ws-skill": "Workspace skill"})
        names = plugin.list_skill_names()
        assert "ws-skill" in names

    def test_plugin_discovers_from_extra_dirs(self, tmp_path: Path):
        extra = tmp_path / "extra-skills"
        extra.mkdir()
        d = extra / "extra-skill"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: extra-skill\ndescription: Extra\n---\nBody"
        )
        no_global = tmp_path / "no-global"
        plugin = SkillsPlugin(
            workspace_root=tmp_path,
            extra_dirs=[str(extra)],
            global_skills_dir=no_global,
        )
        names = plugin.list_skill_names()
        assert "extra-skill" in names

    def test_plugin_workspace_takes_precedence(self, tmp_path: Path):
        ws_agents = tmp_path / ".agents" / "skills" / "dupe"
        ws_agents.mkdir(parents=True)
        (ws_agents / "SKILL.md").write_text(
            "---\nname: dupe\ndescription: WS\n---\nWSBody"
        )
        extra = tmp_path / "extra"
        extra.mkdir()
        d = extra / "dupe"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: dupe\ndescription: Extra\n---\nExtraBody"
        )
        plugin = SkillsPlugin(
            workspace_root=tmp_path,
            extra_dirs=[str(extra)],
            global_skills_dir=tmp_path / "no-global",
        )
        skill = plugin.get_skill("dupe")
        assert skill is not None
        assert "WSBody" in skill.body()


class TestSkillActivationTiming:
    def test_build_context_returns_prompt_after_request_skill(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path, {"test-skill": "A test skill"})
        ctx = FakeCtx()
        result = plugin.request_skill(ctx, "test-skill")
        assert "activated" in result.lower() or "will be" in result.lower()
        messages = plugin.build_context()
        assert len(messages) == 2
        assert "test-skill skill prompt body" in messages[1]["content"].lower()

    def test_pending_skill_cleared_after_build_context(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path, {"test-skill": "A test skill"})
        ctx = FakeCtx()
        plugin.request_skill(ctx, "test-skill")
        plugin.build_context()
        assert plugin._pending_skill_name is None
        messages = plugin.build_context()
        assert len(messages) == 2

    def test_skill_off_clears_both_active_and_pending(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path, {"test-skill": "A test skill"})
        ctx = FakeCtx()
        plugin.request_skill(ctx, "test-skill")
        plugin.deactivate()
        assert plugin._active_skill is None
        assert plugin._pending_skill_name is None
        messages = plugin.build_context()
        assert len(messages) == 1

    def test_on_checkpoint_does_not_activate_pending(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path, {"test-skill": "A test skill"})
        ctx = FakeCtx()
        plugin.request_skill(ctx, "test-skill")
        assert plugin._pending_skill_name == "test-skill"
        plugin.on_checkpoint(ctx=ctx)
        assert plugin._active_skill is None
        assert plugin._pending_skill_name == "test-skill"


class TestPublicAPI:
    def test_list_skill_names(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path, {"alpha": "A", "beta": "B"})
        names = plugin.list_skill_names()
        assert sorted(names) == ["alpha", "beta"]

    def test_list_skills_with_descriptions(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path, {"alpha": "Desc A", "beta": "Desc B"})
        descs = plugin.list_skills_with_descriptions()
        desc_dict = dict(descs)
        assert desc_dict["alpha"] == "Desc A"
        assert desc_dict["beta"] == "Desc B"

    def test_get_skill(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path, {"my-skill": "My desc"})
        skill = plugin.get_skill("my-skill")
        assert skill is not None
        assert skill.name == "my-skill"
        assert skill.description == "My desc"

    def test_get_skill_not_found(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path, {"my-skill": "My desc"})
        assert plugin.get_skill("nonexistent") is None

    def test_active_skill_name_none_by_default(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path, {"test-skill": "Test"})
        assert plugin.active_skill_name is None

    def test_active_skill_name_after_invoke(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path, {"test-skill": "Test"})
        plugin._handle_skill_invoke({"name": "test-skill"})
        assert plugin.active_skill_name == "test-skill"


class TestSkillInvoke:
    def test_invoke_activates_skill(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path, {"my-skill": "Desc"})
        result = plugin._handle_skill_invoke({"name": "my-skill"})
        assert "activated" in result.lower()
        assert plugin.active_skill_name == "my-skill"

    def test_invoke_nonexistent_skill(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path, {"my-skill": "Desc"})
        result = plugin._handle_skill_invoke({"name": "nonexistent"})
        assert "not found" in result.lower()

    def test_invoke_with_inputs_renders_placeholders(self, tmp_path: Path):
        agents_dir = tmp_path / ".agents" / "skills" / "input-skill"
        agents_dir.mkdir(parents=True)
        (agents_dir / "SKILL.md").write_text(
            "---\nname: input-skill\ndescription: Inputs test\n"
            "inputs:\n  - name: scope\n    type: string\n---\n"
            "Review {{scope}} files."
        )
        plugin = SkillsPlugin(
            workspace_root=tmp_path, global_skills_dir=tmp_path / "no-global"
        )
        plugin._handle_skill_invoke(
            {"name": "input-skill", "inputs": {"scope": "*.py"}}
        )
        messages = plugin.build_context()
        assert "Review *.py files." in messages[1]["content"]


class TestSkillList:
    def test_skill_list_returns_formatted(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path, {"skill-a": "Desc A", "skill-b": "Desc B"})
        result = plugin._handle_skill_list()
        assert "skill-a" in result
        assert "skill-b" in result
        assert "Desc A" in result

    def test_skill_list_empty(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path)
        result = plugin._handle_skill_list()
        assert "no skills" in result.lower()


class TestReviewFixes:
    def test_activate_immediately_activates_without_pending(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path, {"my-skill": "Desc"})
        result = plugin.activate_immediately("my-skill")
        assert "activated" in result.lower()
        assert plugin.active_skill_name == "my-skill"
        assert plugin._pending_skill_name is None

    def test_xml_escapes_special_chars_in_description(self, tmp_path: Path):
        agents_dir = tmp_path / ".agents" / "skills" / "bad-skill"
        agents_dir.mkdir(parents=True)
        (agents_dir / "SKILL.md").write_text(
            "---\nname: bad-skill\ndescription: 'A & B <test> \"quotes\"'\n---\nBody"
        )
        plugin = SkillsPlugin(
            workspace_root=tmp_path, global_skills_dir=tmp_path / "no-global"
        )
        messages = plugin.build_context()
        content = messages[0]["content"]
        assert "&amp;" in content
        assert "<test>" not in content

    def test_do_mount_only_exposes_available_skills(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path, {"test-skill": "Test"})
        state = plugin.do_mount()
        assert "available_skills" in state
        assert "active_skill" not in state
        assert "pending_skill" not in state


class TestSkillsPluginSessionEvents:
    def test_hooks_register_on_session_event(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path, {"test-skill": "Test"})

        assert "on_session_event" in plugin.hooks()

    def test_doom_detected_event_deactivates_active_skill(self, tmp_path: Path):
        plugin = _make_plugin(tmp_path, {"test-skill": "Test"})
        plugin.activate_immediately("test-skill")

        plugin.on_session_event(
            event_type="doom_detected",
            payload={"reason": "doom_loop detected"},
        )

        assert plugin.active_skill_name is None

    def test_doom_detector_event_bus_deactivates_skill(self, tmp_path: Path):
        skills = _make_plugin(tmp_path, {"test-skill": "Test"})
        skills.activate_immediately("test-skill")
        doom_detector = DoomDetectorPlugin()

        registry = PluginRegistry(specs=HOOK_SPECS)
        registry.register(doom_detector)
        registry.register(skills)
        runtime = HookRuntime(registry, specs=HOOK_SPECS)

        tape = Tape()
        for _ in range(3):
            tape.append(_make_tool_call("file_read", {"path": "/foo.py"}))
            tape.append(_make_tool_result("file_read"))

        ctx = FakePipelineContext(tape=tape)

        doom_detector.on_checkpoint(ctx=ctx, runtime=runtime)

        assert skills.active_skill_name is None
