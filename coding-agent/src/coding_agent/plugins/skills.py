"""SkillsPlugin — injects skill prompts into context and exposes skill_invoke tool.

A Skill is a markdown file with YAML frontmatter:

    ---
    name: code-review
    description: Review code changes for bugs, security, and style
    inputs:
      - name: scope
        type: string
        description: "File pattern or 'staged' for git staged changes"
    ---
    You are a senior code reviewer. Analyze the following changes: ...

Integration points:
  - build_context : injects the active skill's system prompt before each LLM call
  - get_tools     : exposes `skill_invoke` and `skill_list` tools to the LLM
  - execute_tool  : handles skill_invoke / skill_list calls
  - on_checkpoint : no-op (pending activation is handled entirely by build_context)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from agentkit.tools.schema import ToolSchema

from coding_agent.skills import Skill, SkillLoader

logger = logging.getLogger(__name__)


class SkillsPlugin:
    """Plugin that integrates the Skills system into the agent pipeline."""

    state_key = "skills"

    def __init__(
        self,
        skills_dir: Path | str | None = None,
    ) -> None:
        # Default to ~/.coding-agent/skills or local ./skills
        if skills_dir is None:
            local = Path("./skills")
            home = Path.home() / ".coding-agent" / "skills"
            skills_dir = local if local.exists() else home

        self._loader = SkillLoader(Path(skills_dir))
        self._active_skill: Skill | None = None
        self._pending_skill_name: str | None = None

    # ------------------------------------------------------------------ #
    # Plugin protocol
    # ------------------------------------------------------------------ #

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "build_context": self.build_context,
            "get_tools": self.get_tools,
            "execute_tool": self.execute_tool,
            "on_checkpoint": self.on_checkpoint,
            "mount": self.do_mount,
        }

    def do_mount(self, **kwargs: Any) -> dict[str, Any]:
        """Return initial plugin state."""
        return {
            "active_skill": None,
            "pending_skill": None,
            "available_skills": self._loader.list_skills(),
        }

    # ------------------------------------------------------------------ #
    # build_context — inject active skill prompt
    # ------------------------------------------------------------------ #

    def build_context(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Inject the active skill's body as a system message."""
        # Sole consumer of _pending_skill_name (set by request_skill). Fires during
        # the build_context pipeline stage, BEFORE run_model — ensures the skill
        # prompt is injected on the same turn the user requested it.
        if self._pending_skill_name is not None:
            skill = self._loader.get_skill(self._pending_skill_name)
            if skill is not None:
                self._active_skill = skill
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

        if self._active_skill is None:
            return []

        content = f"[Skill: {self._active_skill.name}]\n\n{self._active_skill.body}"
        logger.debug(
            "SkillsPlugin: injecting skill '%s' into context", self._active_skill.name
        )
        return [{"role": "system", "content": content}]

    # ------------------------------------------------------------------ #
    # get_tools — expose skill_invoke and skill_list to LLM
    # ------------------------------------------------------------------ #

    def get_tools(self, **kwargs: Any) -> list[ToolSchema]:
        """Expose skill management tools."""
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

    # ------------------------------------------------------------------ #
    # execute_tool — handle skill_invoke and skill_list
    # ------------------------------------------------------------------ #

    def execute_tool(
        self,
        name: str = "",
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Handle skill tool calls."""
        if name == "skill_invoke":
            return self._handle_skill_invoke(arguments or {})
        if name == "skill_list":
            return self._handle_skill_list()
        # Not our tool — return None so call_first tries next plugin
        return None

    def _handle_skill_invoke(self, arguments: dict[str, Any]) -> str:
        skill_name = arguments.get("name", "")
        inputs = arguments.get("inputs", {})

        skill = self._loader.get_skill(skill_name)
        if skill is None:
            available = ", ".join(self._loader.list_skills()) or "(none)"
            return f"Skill '{skill_name}' not found. Available skills: {available}"

        # Render inputs into skill body if declared
        body = self._render_skill_body(skill, inputs)
        activated_skill = Skill(
            name=skill.name,
            description=skill.description,
            inputs=skill.inputs,
            body=body,
            source_path=skill.source_path,
        )

        self._active_skill = activated_skill
        logger.info("SkillsPlugin: activated skill '%s'", skill_name)
        return (
            f"Skill '{skill_name}' activated. "
            f"Its instructions will be applied to subsequent steps."
        )

    def _handle_skill_list(self) -> str:
        frontmatters = self._loader.load_all_frontmatters()
        if not frontmatters:
            return "No skills available."

        lines = ["Available skills:\n"]
        for skill_name, fm in frontmatters.items():
            desc = fm.get("description", "(no description)")
            lines.append(f"  - {skill_name}: {desc}")
        return "\n".join(lines)

    def _render_skill_body(self, skill: Skill, inputs: dict[str, Any]) -> str:
        """Simple template substitution: replace {{input_name}} placeholders."""
        body = skill.body
        for inp in skill.inputs:
            placeholder = f"{{{{{inp['name']}}}}}"
            value = str(inputs.get(inp["name"], ""))
            if value:
                body = body.replace(placeholder, value)
        return body

    # ------------------------------------------------------------------ #
    # on_checkpoint — no-op: pending activation handled by build_context()
    # ------------------------------------------------------------------ #

    def on_checkpoint(self, ctx: Any = None, **kwargs: Any) -> None:
        pass

    # ------------------------------------------------------------------ #
    # Public API for CLI layer (e.g. /skill command)
    # ------------------------------------------------------------------ #

    def request_skill(self, ctx: Any, skill_name: str) -> str:
        """Called from CLI /skill command. Sets _pending_skill_name for build_context()."""
        # Single source of truth: _pending_skill_name is consumed by build_context()
        # on the next pipeline turn. build_context() receives only tape= kwarg (not ctx),
        # so _pending_skill_name is the only channel that reaches it.
        available = self._loader.list_skills()
        if skill_name not in available:
            return (
                f"Skill '{skill_name}' not found. "
                f"Available: {', '.join(available) or '(none)'}"
            )
        self._pending_skill_name = skill_name
        return f"Skill '{skill_name}' will be activated on next turn."

    def deactivate(self) -> None:
        """Deactivate the current skill."""
        self._active_skill = None
        self._pending_skill_name = None

    @property
    def active_skill_name(self) -> str | None:
        return self._active_skill.name if self._active_skill else None
