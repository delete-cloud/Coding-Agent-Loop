"""Skills system — .agents/skills/<name>/SKILL.md directory-based discovery."""

import logging
import re
import string
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ── Validation constants ──
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_NAME_LEN = 64
_MAX_DESC_LEN = 1024


# ── New API ──


@dataclass(frozen=True)
class SkillMetadata:
    """A discovered skill with validated metadata."""

    name: str
    description: str
    location: str
    source: str
    _body_text: str = field(repr=False)
    _skill_dir: Path = field(repr=False)
    metadata: dict[str, Any] = field(default_factory=dict)
    inputs: list[dict] = field(default_factory=list)

    def body(self) -> str:
        """Return body with $SKILL_DIR and $PYTHON template variables substituted."""
        return string.Template(self._body_text).safe_substitute(
            SKILL_DIR=str(self._skill_dir),
            PYTHON=sys.executable,
        )


def _parse_frontmatter(
    content: str, source_path: Path | None = None
) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown content."""
    if not content.startswith("---"):
        return {}, content

    end_match = content.find("\n---", 3)
    if end_match == -1:
        return {}, content

    frontmatter_text = content[3:end_match].strip()
    body = content[end_match + 4 :].strip()

    try:
        frontmatter = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError as e:
        path_str = source_path or "unknown"
        logger.warning("Failed to parse frontmatter in %s: %s", path_str, e)
        return {}, body

    return frontmatter, body


def _validate_skill_dir(dirpath: Path, source: str = "extra") -> SkillMetadata | None:
    """Validate a skill directory and return SkillMetadata, or None if invalid."""
    skill_file = dirpath / "SKILL.md"
    if not skill_file.exists():
        return None

    try:
        content = skill_file.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to read %s: %s", skill_file, e)
        return None

    frontmatter, body = _parse_frontmatter(content, skill_file)

    name = frontmatter.get("name")
    if not name or not isinstance(name, str):
        logger.warning(
            "Skill %s: missing or invalid 'name' in frontmatter", dirpath.name
        )
        return None

    if name != dirpath.name:
        logger.warning("Skill %s: name '%s' doesn't match dirname", dirpath, name)
        return None

    if not _NAME_RE.match(name):
        logger.warning(
            "Skill %s: name '%s' doesn't match pattern %s",
            dirpath,
            name,
            _NAME_RE.pattern,
        )
        return None

    if len(name) > _MAX_NAME_LEN:
        logger.warning("Skill %s: name exceeds %d chars", dirpath, _MAX_NAME_LEN)
        return None

    description = frontmatter.get("description", "")
    if not isinstance(description, str):
        description = str(description)
    if len(description) > _MAX_DESC_LEN:
        logger.warning("Skill %s: description exceeds %d chars", dirpath, _MAX_DESC_LEN)
        return None

    meta = {
        k: v
        for k, v in frontmatter.items()
        if k not in ("name", "description", "inputs")
    }
    inputs = frontmatter.get("inputs", [])
    if not isinstance(inputs, list):
        inputs = []

    return SkillMetadata(
        name=name,
        description=description,
        location=f"file://{skill_file.resolve()}",
        source=source,
        _body_text=body,
        _skill_dir=dirpath,
        metadata=meta,
        inputs=inputs,
    )


def discover_skills(
    dirs: list[Path],
    *,
    sources: list[str] | None = None,
) -> list[SkillMetadata]:
    """Discover skills from multiple directories with first-wins dedup.

    Scans each directory for subdirectories containing SKILL.md.
    Deduplicates by name.casefold() — first directory wins.
    """
    seen: set[str] = set()
    skills: list[SkillMetadata] = []

    for i, search_dir in enumerate(dirs):
        if not search_dir.exists() or not search_dir.is_dir():
            continue

        source = sources[i] if sources and i < len(sources) else "extra"

        try:
            entries = sorted(search_dir.iterdir())
        except OSError as e:
            logger.warning("Failed to scan %s: %s", search_dir, e)
            continue

        for entry in entries:
            if not entry.is_dir():
                continue

            skill = _validate_skill_dir(entry, source=source)
            if skill is None:
                continue

            key = skill.name.casefold()
            if key in seen:
                logger.debug("Skipping duplicate skill '%s' from %s", skill.name, entry)
                continue

            seen.add(key)
            skills.append(skill)

    return skills


# ── Deprecated API (backward compat until plugins/skills.py is updated in Task 3) ──


@dataclass
class Skill:
    """DEPRECATED: Use SkillMetadata instead."""

    name: str
    description: str
    inputs: list[dict]
    body: str
    source_path: Path


class SkillLoader:
    """DEPRECATED: Use discover_skills() instead."""

    def __init__(self, skills_dir: Path | str):
        self.skills_dir = Path(skills_dir)
        self._cache: dict[str, Skill] = {}
        self._frontmatters: dict[str, dict] = {}
        self._body_paths: dict[str, Path] = {}

    def _load_skill_file(self, path: Path) -> tuple[str, dict, str] | None:
        if not path.exists():
            return None
        content = path.read_text(encoding="utf-8")
        frontmatter, body = _parse_frontmatter(content, path)
        name = frontmatter.get("name")
        if not name:
            name = path.stem
        return name, frontmatter, body

    def load_all_frontmatters(self) -> dict[str, dict]:
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
        if name in self._cache:
            return self._cache[name]
        if not self._frontmatters:
            self.load_all_frontmatters()
        if name not in self._body_paths:
            return None
        path = self._body_paths[name]
        result = self._load_skill_file(path)
        if result is None:
            return None
        _, frontmatter, body = result
        skill = Skill(
            name=name,
            description=frontmatter.get("description", ""),
            inputs=frontmatter.get("inputs", []),
            body=body,
            source_path=path,
        )
        self._cache[name] = skill
        return skill

    def list_skills(self) -> list[str]:
        if not self._frontmatters:
            self.load_all_frontmatters()
        return list(self._frontmatters.keys())

    def clear_cache(self) -> None:
        self._cache.clear()
