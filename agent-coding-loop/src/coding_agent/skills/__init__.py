"""Skills system - lazy loading of SKILL.md files."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Skill:
    """A loaded skill with frontmatter metadata and markdown body."""
    name: str
    description: str
    inputs: list[dict]
    body: str
    source_path: Path


class SkillLoader:
    """Lazy loader for SKILL.md files."""

    def __init__(self, skills_dir: Path | str):
        self.skills_dir = Path(skills_dir)
        self._cache: dict[str, Skill] = {}
        self._frontmatters: dict[str, dict] = {}
        self._body_paths: dict[str, Path] = {}

    def _parse_frontmatter(self, content: str) -> tuple[dict, str]:
        """Parse YAML frontmatter from markdown content.
        
        Returns a tuple of (frontmatter_dict, body).
        """
        if not content.startswith("---"):
            return {}, content

        # Find the end of frontmatter
        end_match = content.find("\n---", 3)
        if end_match == -1:
            return {}, content

        frontmatter_text = content[3:end_match].strip()
        body = content[end_match + 4:].strip()

        try:
            frontmatter = yaml.safe_load(frontmatter_text) or {}
        except yaml.YAMLError:
            frontmatter = {}

        return frontmatter, body

    def _load_skill_file(self, path: Path) -> tuple[str, dict, str] | None:
        """Load and parse a skill file.
        
        Returns (name, frontmatter, body) or None if invalid.
        """
        if not path.exists():
            return None

        content = path.read_text(encoding="utf-8")
        frontmatter, body = self._parse_frontmatter(content)

        name = frontmatter.get("name")
        if not name:
            # Use filename without extension as fallback
            name = path.stem

        return name, frontmatter, body

    def load_all_frontmatters(self) -> dict[str, dict]:
        """Eagerly load only frontmatter from all skills (fast)."""
        self._frontmatters = {}
        self._body_paths = {}

        if not self.skills_dir.exists():
            return self._frontmatters

        for skill_file in self.skills_dir.glob("*.md"):
            result = self._load_skill_file(skill_file)
            if result is None:
                continue

            name, frontmatter, _ = result
            self._frontmatters[name] = frontmatter
            self._body_paths[name] = skill_file

        return self._frontmatters

    def get_skill(self, name: str) -> Skill | None:
        """Lazy load full skill body on demand."""
        # Return from cache if already loaded
        if name in self._cache:
            return self._cache[name]

        # Load frontmatters if not already loaded
        if not self._frontmatters:
            self.load_all_frontmatters()

        # Check if skill exists
        if name not in self._body_paths:
            return None

        # Load full content
        path = self._body_paths[name]
        result = self._load_skill_file(path)
        if result is None:
            return None

        _, frontmatter, body = result

        # Create Skill object
        skill = Skill(
            name=name,
            description=frontmatter.get("description", ""),
            inputs=frontmatter.get("inputs", []),
            body=body,
            source_path=path,
        )

        # Cache the skill
        self._cache[name] = skill
        return skill

    def list_skills(self) -> list[str]:
        """List available skill names."""
        if not self._frontmatters:
            self.load_all_frontmatters()
        return list(self._frontmatters.keys())

    def clear_cache(self) -> None:
        """Clear the skill cache."""
        self._cache.clear()
