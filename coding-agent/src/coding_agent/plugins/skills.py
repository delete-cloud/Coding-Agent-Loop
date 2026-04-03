"""SkillsPlugin — summary mode context injection + skill_invoke/skill_list tools.

Discovers skills from .agents/skills/ directories (project → global → extra),
injects <available_skills> XML summary into every LLM call, and provides
skill_invoke/skill_list tools for activation.
"""

from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import Any, Callable

from agentkit.tools.schema import ToolSchema

from coding_agent.skills import SkillMetadata, discover_skills

logger = logging.getLogger(__name__)


class SkillsPlugin:
    state_key = "skills"

    def __init__(
        self,
        workspace_root: Path | None = None,
        extra_dirs: list[str] | None = None,
        global_skills_dir: Path | None = None,
    ) -> None:
        workspace_root = workspace_root or Path.cwd()
        dirs: list[Path] = []
        sources: list[str] = []

        project_dir = workspace_root / ".agents" / "skills"
        dirs.append(project_dir)
        sources.append("project")

        global_dir = (
            global_skills_dir
            if global_skills_dir is not None
            else Path.home() / ".agents" / "skills"
        )
        dirs.append(global_dir)
        sources.append("global")

        for extra in extra_dirs or []:
            dirs.append(Path(extra))
            sources.append("extra")

        discovered = discover_skills(dirs, sources=sources)
        self._skills: dict[str, SkillMetadata] = {s.name: s for s in discovered}
        self._active_skill: SkillMetadata | None = None
        self._active_rendered_body: str | None = None
        self._pending_skill_name: str | None = None

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "build_context": self.build_context,
            "get_tools": self.get_tools,
            "execute_tool": self.execute_tool,
            "on_checkpoint": self.on_checkpoint,
            "mount": self.do_mount,
        }

    def do_mount(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "available_skills": list(self._skills.keys()),
        }

    def build_context(self, **kwargs: Any) -> list[dict[str, Any]]:
        if self._pending_skill_name is not None:
            skill = self._skills.get(self._pending_skill_name)
            if skill is not None:
                self._active_skill = skill
                self._active_rendered_body = skill.body()
                logger.info(
                    "SkillsPlugin: activated pending skill '%s' in build_context",
                    self._pending_skill_name,
                )
            else:
                logger.warning(
                    "SkillsPlugin: pending skill '%s' not found",
                    self._pending_skill_name,
                )
            self._pending_skill_name = None

        messages: list[dict[str, Any]] = []

        if self._skills:
            lines = ["<available_skills>"]
            for skill in self._skills.values():
                lines.append("  <skill>")
                lines.append(f"    <name>{skill.name}</name>")
                lines.append(
                    f"    <description>{html.escape(skill.description)}</description>"
                )
                lines.append(f"    <location>{html.escape(skill.location)}</location>")
                lines.append("  </skill>")
            lines.append("</available_skills>")
            messages.append({"role": "system", "content": "\n".join(lines)})

        if self._active_skill is not None:
            body = self._active_rendered_body or self._active_skill.body()
            content = f"[Skill: {self._active_skill.name}]\n\n{body}"
            messages.append({"role": "system", "content": content})

        return messages

    def get_tools(self, **kwargs: Any) -> list[ToolSchema]:
        return [
            ToolSchema(
                name="skill_invoke",
                description=(
                    "Activate a named skill to apply its specialized instructions "
                    "for the current task. The skill's prompt will be injected into "
                    "subsequent context. Use skill_list to see available skills."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Name of the skill to activate",
                        },
                        "inputs": {
                            "type": "object",
                            "description": (
                                "Optional input values for the skill's declared inputs "
                                "(key-value pairs matching the skill's frontmatter inputs)"
                            ),
                        },
                    },
                    "required": ["name"],
                },
            ),
            ToolSchema(
                name="skill_list",
                description="List all available skills with their descriptions.",
                parameters={
                    "type": "object",
                    "properties": {},
                },
            ),
        ]

    def execute_tool(
        self,
        name: str = "",
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        if name == "skill_invoke":
            return self._handle_skill_invoke(arguments or {})
        if name == "skill_list":
            return self._handle_skill_list()
        return None

    def _handle_skill_invoke(self, arguments: dict[str, Any]) -> str:
        skill_name = arguments.get("name", "")
        inputs = arguments.get("inputs", {})

        skill = self._skills.get(skill_name)
        if skill is None:
            available = ", ".join(self._skills.keys()) or "(none)"
            return f"Skill '{skill_name}' not found. Available skills: {available}"

        body = self._render_skill_body(skill, inputs)
        self._active_skill = skill
        self._active_rendered_body = body
        logger.info("SkillsPlugin: activated skill '%s'", skill_name)
        return (
            f"Skill '{skill_name}' activated. "
            f"Its instructions will be applied to subsequent steps."
        )

    def _handle_skill_list(self) -> str:
        if not self._skills:
            return "No skills available."

        lines = ["Available skills:\n"]
        for name, skill in self._skills.items():
            lines.append(f"  - {name}: {skill.description}")
        return "\n".join(lines)

    def _render_skill_body(self, skill: SkillMetadata, inputs: dict[str, Any]) -> str:
        body = skill.body()
        for inp in skill.inputs:
            placeholder = f"{{{{{inp['name']}}}}}"
            value = str(inputs.get(inp["name"], ""))
            if value:
                body = body.replace(placeholder, value)
        return body

    def on_checkpoint(self, ctx: Any = None, **kwargs: Any) -> None:
        pass

    def request_skill(self, ctx: Any, skill_name: str) -> str:
        if skill_name not in self._skills:
            available = ", ".join(self._skills.keys()) or "(none)"
            return f"Skill '{skill_name}' not found. Available: {available}"
        self._pending_skill_name = skill_name
        return f"Skill '{skill_name}' will be activated on next turn."

    def deactivate(self) -> None:
        self._active_skill = None
        self._active_rendered_body = None
        self._pending_skill_name = None

    @property
    def active_skill_name(self) -> str | None:
        return self._active_skill.name if self._active_skill else None

    def list_skill_names(self) -> list[str]:
        return list(self._skills.keys())

    def list_skills_with_descriptions(self) -> list[tuple[str, str]]:
        return [(s.name, s.description) for s in self._skills.values()]

    def get_skill(self, name: str) -> SkillMetadata | None:
        return self._skills.get(name)

    def activate_immediately(
        self, skill_name: str, inputs: dict[str, Any] | None = None
    ) -> str:
        return self._handle_skill_invoke({"name": skill_name, "inputs": inputs or {}})
