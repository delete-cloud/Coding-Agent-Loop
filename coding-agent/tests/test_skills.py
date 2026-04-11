"""Tests for the skills system."""

import tempfile
from pathlib import Path

import pytest

from coding_agent.skills import Skill, SkillLoader


class TestFrontmatterParsing:
    """Test frontmatter parsing functionality."""

    def test_parse_simple_frontmatter(self):
        """Test parsing a simple frontmatter block."""
        loader = SkillLoader("/tmp")
        content = """---
name: test-skill
description: A test skill
---

This is the body."""

        frontmatter, body = loader._parse_frontmatter(content)

        assert frontmatter["name"] == "test-skill"
        assert frontmatter["description"] == "A test skill"
        assert body == "This is the body."

    def test_parse_frontmatter_with_inputs(self):
        """Test parsing frontmatter with inputs array."""
        loader = SkillLoader("/tmp")
        content = """---
name: test-skill
description: A test skill
inputs:
  - name: input1
    type: string
    description: First input
  - name: input2
    type: int
    description: Second input
---

Body content here."""

        frontmatter, body = loader._parse_frontmatter(content)

        assert frontmatter["name"] == "test-skill"
        assert len(frontmatter["inputs"]) == 2
        assert frontmatter["inputs"][0]["name"] == "input1"
        assert frontmatter["inputs"][1]["type"] == "int"
        assert body == "Body content here."

    def test_parse_no_frontmatter(self):
        """Test parsing content without frontmatter."""
        loader = SkillLoader("/tmp")
        content = "Just a body without frontmatter."

        frontmatter, body = loader._parse_frontmatter(content)

        assert frontmatter == {}
        assert body == content

    def test_parse_empty_frontmatter(self):
        """Test parsing content with empty frontmatter."""
        loader = SkillLoader("/tmp")
        content = """---
---

Body only."""

        frontmatter, body = loader._parse_frontmatter(content)

        assert frontmatter == {}
        assert body == "Body only."


class TestLazyLoading:
    """Test lazy loading behavior."""

    @pytest.fixture
    def temp_skills_dir(self):
        """Create a temporary directory with test skill files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)

            # Create first skill
            (skills_dir / "skill1.md").write_text("""---
name: skill1
description: First test skill
inputs:
  - name: param1
    type: string
---

Skill 1 body content.
""")

            # Create second skill
            (skills_dir / "skill2.md").write_text("""---
name: skill2
description: Second test skill
---

Skill 2 body content.
More lines here.
""")

            yield skills_dir

    def test_load_all_frontmatters_only_loads_frontmatter(self, temp_skills_dir):
        """Test that load_all_frontmatters only reads frontmatter, not body."""
        loader = SkillLoader(temp_skills_dir)
        frontmatters = loader.load_all_frontmatters()

        assert "skill1" in frontmatters
        assert "skill2" in frontmatters
        assert frontmatters["skill1"]["description"] == "First test skill"
        assert frontmatters["skill2"]["description"] == "Second test skill"

        # Cache should be empty since we only loaded frontmatters
        assert len(loader._cache) == 0

    def test_get_skill_lazy_loads_body(self, temp_skills_dir):
        """Test that get_skill lazily loads the full skill body."""
        loader = SkillLoader(temp_skills_dir)

        # First, load frontmatters
        loader.load_all_frontmatters()
        assert len(loader._cache) == 0  # Not cached yet

        # Now get a skill
        skill = loader.get_skill("skill1")

        assert skill is not None
        assert skill.name == "skill1"
        assert skill.description == "First test skill"
        assert skill.body == "Skill 1 body content."
        assert len(skill.inputs) == 1

        # Should be cached now
        assert "skill1" in loader._cache

    def test_get_skill_returns_cached_instance(self, temp_skills_dir):
        """Test that get_skill returns the same cached instance."""
        loader = SkillLoader(temp_skills_dir)

        skill1_first = loader.get_skill("skill1")
        skill1_second = loader.get_skill("skill1")

        assert skill1_first is skill1_second

    def test_get_skill_not_found(self, temp_skills_dir):
        """Test that get_skill returns None for unknown skills."""
        loader = SkillLoader(temp_skills_dir)

        result = loader.get_skill("nonexistent")

        assert result is None

    def test_get_skill_auto_loads_frontmatters(self, temp_skills_dir):
        """Test that get_skill auto-loads frontmatters if not loaded."""
        loader = SkillLoader(temp_skills_dir)

        # Don't call load_all_frontmatters first
        skill = loader.get_skill("skill1")

        assert skill is not None
        assert skill.name == "skill1"


class TestSkillLookup:
    """Test skill lookup functionality."""

    @pytest.fixture
    def temp_skills_dir(self):
        """Create a temporary directory with test skill files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)

            (skills_dir / "code-review.md").write_text("""---
name: code-review
description: Review code
---

Code review body.
""")

            (skills_dir / "test-writing.md").write_text("""---
name: test-writing
description: Write tests
---

Test writing body.
""")

            yield skills_dir

    def test_list_skills(self, temp_skills_dir):
        """Test listing all available skills."""
        loader = SkillLoader(temp_skills_dir)
        skills = loader.list_skills()

        assert len(skills) == 2
        assert "code-review" in skills
        assert "test-writing" in skills

    def test_list_skills_empty_dir(self):
        """Test listing skills in an empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = SkillLoader(tmpdir)
            skills = loader.list_skills()

            assert skills == []

    def test_list_skills_nonexistent_dir(self):
        """Test listing skills in a non-existent directory."""
        loader = SkillLoader("/nonexistent/path/that/does/not/exist")
        skills = loader.list_skills()

        assert skills == []


class TestCaching:
    """Test caching behavior."""

    @pytest.fixture
    def temp_skills_dir(self):
        """Create a temporary directory with a test skill file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            (skills_dir / "cached-skill.md").write_text("""---
name: cached-skill
description: A cached skill
---

Original body.
""")
            yield skills_dir

    def test_clear_cache(self, temp_skills_dir):
        """Test clearing the skill cache."""
        loader = SkillLoader(temp_skills_dir)

        # Load a skill
        skill = loader.get_skill("cached-skill")
        assert skill is not None
        assert "cached-skill" in loader._cache

        # Clear cache
        loader.clear_cache()
        assert len(loader._cache) == 0

        # Getting skill again should still work
        skill2 = loader.get_skill("cached-skill")
        assert skill2 is not None
        assert "cached-skill" in loader._cache


class TestSkillDataclass:
    """Test Skill dataclass."""

    def test_skill_creation(self):
        """Test creating a Skill instance."""
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
        assert str(skill.source_path) == "/tmp/test.md"


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_skill_without_name_uses_filename(self):
        """Test that skills without name in frontmatter use filename."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            (skills_dir / "unnamed-skill.md").write_text("""---
description: Skill without name
---

Body content.
""")

            loader = SkillLoader(skills_dir)
            skills = loader.list_skills()

            assert "unnamed-skill" in skills

    def test_invalid_yaml_frontmatter(self):
        """Test handling of invalid YAML in frontmatter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            (skills_dir / "invalid.md").write_text("""---
name: test
invalid: [unclosed bracket
---

Body.
""")

            loader = SkillLoader(skills_dir)
            # Should not raise, but treat as empty frontmatter
            skills = loader.list_skills()

            # The skill should still be listed (by filename)
            assert "invalid" in skills

    def test_empty_skills_directory(self):
        """Test handling of empty skills directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = SkillLoader(tmpdir)
            skills = loader.list_skills()

            assert skills == []

    def test_nonexistent_skill_file(self):
        """Test handling of nonexistent skill file."""
        loader = SkillLoader("/tmp")
        result = loader._load_skill_file(Path("/nonexistent/path.md"))

        assert result is None
