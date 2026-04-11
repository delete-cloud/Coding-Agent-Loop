"""Tests for the skills system — directory-based .agents/skills/<name>/SKILL.md format."""

import sys
import tempfile
from pathlib import Path

import pytest

from coding_agent.skills import (
    Skill,
    SkillLoader,
    SkillMetadata,
    _parse_frontmatter,
    _validate_skill_dir,
    discover_skills,
)


def _make_skill_dir(
    parent: Path,
    name: str,
    *,
    description: str = "A test skill",
    body: str = "Skill body.",
    extra_fm: str = "",
    inputs: str = "",
) -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    fm_parts = [f"name: {name}", f"description: {description}"]
    if inputs:
        fm_parts.append(inputs)
    if extra_fm:
        fm_parts.append(extra_fm)
    fm_block = "\n".join(fm_parts)
    (d / "SKILL.md").write_text(f"---\n{fm_block}\n---\n{body}")
    return d


class TestParseFrontmatter:
    def test_parse_simple_frontmatter(self):
        content = (
            "---\nname: test-skill\ndescription: A test skill\n---\nThis is the body."
        )
        fm, body = _parse_frontmatter(content)
        assert fm["name"] == "test-skill"
        assert fm["description"] == "A test skill"
        assert body == "This is the body."

    def test_parse_frontmatter_with_inputs(self):
        content = (
            "---\nname: test-skill\ndescription: A test skill\n"
            "inputs:\n  - name: input1\n    type: string\n  - name: input2\n    type: int\n"
            "---\nBody content here."
        )
        fm, body = _parse_frontmatter(content)
        assert fm["name"] == "test-skill"
        assert len(fm["inputs"]) == 2
        assert fm["inputs"][0]["name"] == "input1"
        assert fm["inputs"][1]["type"] == "int"
        assert body == "Body content here."

    def test_parse_no_frontmatter(self):
        content = "Just a body without frontmatter."
        fm, body = _parse_frontmatter(content)
        assert fm == {}
        assert body == content

    def test_parse_empty_frontmatter(self):
        content = "---\n---\nBody only."
        fm, body = _parse_frontmatter(content)
        assert fm == {}
        assert body == "Body only."

    def test_parse_invalid_yaml(self):
        content = "---\nname: test\ninvalid: [unclosed bracket\n---\nBody."
        fm, body = _parse_frontmatter(content)
        assert fm == {}
        assert body == "Body."

    def test_parse_no_closing_fence(self):
        content = "---\nname: test\nThis never closes"
        fm, body = _parse_frontmatter(content)
        assert fm == {}
        assert body == content


class TestSkillMetadata:
    def test_creation_and_field_access(self):
        skill = SkillMetadata(
            name="my-skill",
            description="A skill",
            location="file:///tmp/my-skill/SKILL.md",
            source="project",
            _body_text="Raw body",
            _skill_dir=Path("/tmp/my-skill"),
        )
        assert skill.name == "my-skill"
        assert skill.description == "A skill"
        assert skill.location == "file:///tmp/my-skill/SKILL.md"
        assert skill.source == "project"

    def test_frozen(self):
        skill = SkillMetadata(
            name="frozen",
            description="Frozen skill",
            location="file:///tmp/frozen/SKILL.md",
            source="global",
            _body_text="body",
            _skill_dir=Path("/tmp/frozen"),
        )
        with pytest.raises(AttributeError):
            skill.name = "mutated"  # type: ignore[misc]

    def test_body_template_substitution(self):
        skill_dir = Path("/some/dir/my-skill")
        skill = SkillMetadata(
            name="my-skill",
            description="Template test",
            location="file:///some/dir/my-skill/SKILL.md",
            source="project",
            _body_text="Dir: $SKILL_DIR\nPython: $PYTHON",
            _skill_dir=skill_dir,
        )
        body = skill.body()
        assert str(skill_dir) in body
        assert sys.executable in body
        assert "$SKILL_DIR" not in body
        assert "$PYTHON" not in body

    def test_body_safe_substitute_unknown_vars(self):
        skill = SkillMetadata(
            name="safe",
            description="Safe sub",
            location="file:///tmp/safe/SKILL.md",
            source="extra",
            _body_text="Keep $UNKNOWN intact",
            _skill_dir=Path("/tmp/safe"),
        )
        assert "$UNKNOWN" in skill.body()

    def test_default_metadata_and_inputs(self):
        skill = SkillMetadata(
            name="defaults",
            description="Check defaults",
            location="file:///tmp/defaults/SKILL.md",
            source="global",
            _body_text="body",
            _skill_dir=Path("/tmp/defaults"),
        )
        assert skill.metadata == {}
        assert skill.inputs == []

    def test_custom_metadata_and_inputs(self):
        skill = SkillMetadata(
            name="custom",
            description="Custom fields",
            location="file:///tmp/custom/SKILL.md",
            source="project",
            _body_text="body",
            _skill_dir=Path("/tmp/custom"),
            metadata={"author": "test"},
            inputs=[{"name": "x", "type": "string"}],
        )
        assert skill.metadata == {"author": "test"}
        assert skill.inputs[0]["name"] == "x"


class TestValidateSkillDir:
    def test_valid_skill_dir(self, tmp_path: Path):
        _make_skill_dir(tmp_path, "valid-skill")
        result = _validate_skill_dir(tmp_path / "valid-skill", source="project")
        assert result is not None
        assert result.name == "valid-skill"
        assert result.source == "project"

    def test_missing_skill_md(self, tmp_path: Path):
        d = tmp_path / "no-file"
        d.mkdir()
        assert _validate_skill_dir(d) is None

    def test_name_must_match_dirname(self, tmp_path: Path):
        d = tmp_path / "dir-name"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: different-name\ndescription: Mismatch\n---\nBody"
        )
        assert _validate_skill_dir(d) is None

    def test_name_regex_rejects_uppercase(self, tmp_path: Path):
        d = tmp_path / "BadName"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: BadName\ndescription: Bad\n---\nBody")
        assert _validate_skill_dir(d) is None

    def test_name_regex_rejects_underscore(self, tmp_path: Path):
        d = tmp_path / "bad_name"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: bad_name\ndescription: Bad\n---\nBody")
        assert _validate_skill_dir(d) is None

    def test_name_regex_rejects_spaces(self, tmp_path: Path):
        d = tmp_path / "bad name"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: bad name\ndescription: Bad\n---\nBody")
        assert _validate_skill_dir(d) is None

    def test_name_too_long(self, tmp_path: Path):
        long_name = "a" * 65
        d = tmp_path / long_name
        d.mkdir()
        (d / "SKILL.md").write_text(
            f"---\nname: {long_name}\ndescription: Too long\n---\nBody"
        )
        assert _validate_skill_dir(d) is None

    def test_name_max_length_ok(self, tmp_path: Path):
        name = "a" * 64
        d = tmp_path / name
        d.mkdir()
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Max length\n---\nBody"
        )
        result = _validate_skill_dir(d)
        assert result is not None
        assert result.name == name

    def test_description_too_long(self, tmp_path: Path):
        d = tmp_path / "long-desc"
        d.mkdir()
        long_desc = "x" * 1025
        (d / "SKILL.md").write_text(
            f"---\nname: long-desc\ndescription: {long_desc}\n---\nBody"
        )
        assert _validate_skill_dir(d) is None

    def test_description_max_length_ok(self, tmp_path: Path):
        d = tmp_path / "max-desc"
        d.mkdir()
        desc = "x" * 1024
        (d / "SKILL.md").write_text(
            f"---\nname: max-desc\ndescription: {desc}\n---\nBody"
        )
        result = _validate_skill_dir(d)
        assert result is not None

    def test_missing_name_in_frontmatter(self, tmp_path: Path):
        d = tmp_path / "no-name"
        d.mkdir()
        (d / "SKILL.md").write_text("---\ndescription: No name\n---\nBody")
        assert _validate_skill_dir(d) is None

    def test_invalid_yaml_skipped(self, tmp_path: Path):
        d = tmp_path / "bad-yaml"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: bad-yaml\ninvalid: [unclosed\n---\nBody"
        )
        assert _validate_skill_dir(d) is None

    def test_location_is_file_uri(self, tmp_path: Path):
        _make_skill_dir(tmp_path, "uri-test")
        result = _validate_skill_dir(tmp_path / "uri-test")
        assert result is not None
        assert result.location.startswith("file://")
        assert "uri-test/SKILL.md" in result.location

    def test_extra_frontmatter_goes_to_metadata(self, tmp_path: Path):
        d = tmp_path / "meta-test"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: meta-test\ndescription: Meta\nauthor: tester\ntags:\n  - a\n  - b\n---\nBody"
        )
        result = _validate_skill_dir(d)
        assert result is not None
        assert result.metadata["author"] == "tester"
        assert result.metadata["tags"] == ["a", "b"]

    def test_inputs_field_parsed(self, tmp_path: Path):
        _make_skill_dir(
            tmp_path,
            "with-inputs",
            inputs="inputs:\n  - name: x\n    type: string\n  - name: y\n    type: int",
        )
        result = _validate_skill_dir(tmp_path / "with-inputs")
        assert result is not None
        assert len(result.inputs) == 2
        assert result.inputs[0]["name"] == "x"

    def test_non_list_inputs_coerced_to_empty(self, tmp_path: Path):
        d = tmp_path / "bad-inputs"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: bad-inputs\ndescription: Bad inputs\ninputs: not-a-list\n---\nBody"
        )
        result = _validate_skill_dir(d)
        assert result is not None
        assert result.inputs == []


class TestDiscoverSkills:
    def test_single_dir_single_skill(self, tmp_path: Path):
        _make_skill_dir(tmp_path, "alpha")
        result = discover_skills([tmp_path])
        assert len(result) == 1
        assert result[0].name == "alpha"

    def test_single_dir_multiple_skills(self, tmp_path: Path):
        _make_skill_dir(tmp_path, "alpha")
        _make_skill_dir(tmp_path, "beta")
        _make_skill_dir(tmp_path, "gamma")
        result = discover_skills([tmp_path])
        names = {s.name for s in result}
        assert names == {"alpha", "beta", "gamma"}

    def test_multiple_dirs(self, tmp_path: Path):
        dir_a = tmp_path / "dir-a"
        dir_b = tmp_path / "dir-b"
        dir_a.mkdir()
        dir_b.mkdir()
        _make_skill_dir(dir_a, "skill-a")
        _make_skill_dir(dir_b, "skill-b")
        result = discover_skills([dir_a, dir_b])
        names = {s.name for s in result}
        assert names == {"skill-a", "skill-b"}

    def test_sources_labeling(self, tmp_path: Path):
        dir_p = tmp_path / "project"
        dir_g = tmp_path / "global"
        dir_p.mkdir()
        dir_g.mkdir()
        _make_skill_dir(dir_p, "proj-skill")
        _make_skill_dir(dir_g, "glob-skill")
        result = discover_skills([dir_p, dir_g], sources=["project", "global"])
        source_map = {s.name: s.source for s in result}
        assert source_map["proj-skill"] == "project"
        assert source_map["glob-skill"] == "global"

    def test_nonexistent_dir_skipped(self, tmp_path: Path):
        _make_skill_dir(tmp_path, "real-skill")
        result = discover_skills([tmp_path / "does-not-exist", tmp_path])
        assert len(result) == 1
        assert result[0].name == "real-skill"

    def test_empty_dir_returns_empty(self, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        result = discover_skills([empty])
        assert result == []

    def test_empty_dirs_list(self):
        result = discover_skills([])
        assert result == []

    def test_skips_non_directory_entries(self, tmp_path: Path):
        _make_skill_dir(tmp_path, "valid-skill")
        (tmp_path / "not-a-dir.txt").write_text("plain file")
        result = discover_skills([tmp_path])
        assert len(result) == 1
        assert result[0].name == "valid-skill"

    def test_skips_invalid_skills(self, tmp_path: Path):
        _make_skill_dir(tmp_path, "good-skill")
        # Invalid: uppercase name
        bad = tmp_path / "BadSkill"
        bad.mkdir()
        (bad / "SKILL.md").write_text(
            "---\nname: BadSkill\ndescription: Bad\n---\nBody"
        )
        result = discover_skills([tmp_path])
        assert len(result) == 1
        assert result[0].name == "good-skill"

    def test_sorted_iteration(self, tmp_path: Path):
        for name in ["zebra", "alpha", "middle"]:
            _make_skill_dir(tmp_path, name)
        result = discover_skills([tmp_path])
        names = [s.name for s in result]
        assert names == sorted(names)


class TestDeduplication:
    def test_first_dir_wins(self, tmp_path: Path):
        dir1 = tmp_path / "first"
        dir2 = tmp_path / "second"
        dir1.mkdir()
        dir2.mkdir()
        _make_skill_dir(dir1, "dupe-skill", body="FIRST")
        _make_skill_dir(dir2, "dupe-skill", body="SECOND")
        result = discover_skills([dir1, dir2])
        assert len(result) == 1
        assert "FIRST" in result[0].body()

    def test_dedup_case_insensitive(self, tmp_path: Path):
        dir1 = tmp_path / "first"
        dir2 = tmp_path / "second"
        dir1.mkdir()
        dir2.mkdir()
        _make_skill_dir(dir1, "my-skill", body="FIRST")
        _make_skill_dir(dir2, "my-skill", body="SECOND")
        result = discover_skills([dir1, dir2])
        assert len(result) == 1

    def test_different_names_not_deduped(self, tmp_path: Path):
        dir1 = tmp_path / "first"
        dir2 = tmp_path / "second"
        dir1.mkdir()
        dir2.mkdir()
        _make_skill_dir(dir1, "skill-a")
        _make_skill_dir(dir2, "skill-b")
        result = discover_skills([dir1, dir2])
        assert len(result) == 2


class TestEdgeCases:
    def test_empty_body(self, tmp_path: Path):
        d = tmp_path / "empty-body"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: empty-body\ndescription: Empty\n---\n")
        result = _validate_skill_dir(d)
        assert result is not None
        assert result.body() == ""

    def test_skill_md_without_frontmatter(self, tmp_path: Path):
        d = tmp_path / "no-fm"
        d.mkdir()
        (d / "SKILL.md").write_text("Just body content, no frontmatter")
        assert _validate_skill_dir(d) is None

    def test_broken_symlink_skipped(self, tmp_path: Path):
        _make_skill_dir(tmp_path, "good-skill")
        broken = tmp_path / "broken-link"
        broken.symlink_to(tmp_path / "nonexistent-target")
        result = discover_skills([tmp_path])
        assert len(result) == 1
        assert result[0].name == "good-skill"

    def test_permission_error_on_dir_skipped(self, tmp_path: Path):
        _make_skill_dir(tmp_path, "accessible")
        result = discover_skills([tmp_path, tmp_path / "no-access"])
        assert len(result) == 1

    def test_body_with_template_vars(self, tmp_path: Path):
        _make_skill_dir(
            tmp_path,
            "tpl-skill",
            body="Dir: $SKILL_DIR\nPython: $PYTHON\nKeep: $OTHER",
        )
        result = discover_skills([tmp_path])
        body = result[0].body()
        assert str(tmp_path / "tpl-skill") in body
        assert sys.executable in body
        assert "$OTHER" in body


class TestBackwardCompat:
    def test_skill_dataclass_creation(self):
        skill = Skill(
            name="test",
            description="Test skill",
            inputs=[{"name": "input1", "type": "string"}],
            body="Body content",
            source_path=Path("/tmp/test.md"),
        )
        assert skill.name == "test"
        assert skill.description == "Test skill"
        assert len(skill.inputs) == 1
        assert skill.body == "Body content"

    def test_skill_loader_load_frontmatters(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "skill1.md").write_text(
                "---\nname: skill1\ndescription: First\n---\nBody1"
            )
            (d / "skill2.md").write_text(
                "---\nname: skill2\ndescription: Second\n---\nBody2"
            )
            loader = SkillLoader(d)
            fm = loader.load_all_frontmatters()
            assert "skill1" in fm
            assert "skill2" in fm
            assert fm["skill1"]["description"] == "First"

    def test_skill_loader_get_skill(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "my-skill.md").write_text(
                "---\nname: my-skill\ndescription: Test\n---\nSkill body"
            )
            loader = SkillLoader(d)
            skill = loader.get_skill("my-skill")
            assert skill is not None
            assert skill.name == "my-skill"
            assert skill.body == "Skill body"

    def test_skill_loader_list_skills(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "a.md").write_text("---\nname: a\ndescription: A\n---\nBody")
            (d / "b.md").write_text("---\nname: b\ndescription: B\n---\nBody")
            loader = SkillLoader(d)
            skills = loader.list_skills()
            assert "a" in skills
            assert "b" in skills

    def test_skill_loader_nonexistent_dir(self):
        loader = SkillLoader("/nonexistent/path")
        assert loader.list_skills() == []
        assert loader.get_skill("any") is None

    def test_skill_loader_clear_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "cached.md").write_text("---\nname: cached\ndescription: C\n---\nBody")
            loader = SkillLoader(d)
            loader.get_skill("cached")
            assert "cached" in loader._cache
            loader.clear_cache()
            assert len(loader._cache) == 0
