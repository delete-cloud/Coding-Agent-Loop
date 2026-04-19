from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from coding_agent.postmortem_phase1 import (
    build_phase1_artifacts,
    collect_fix_commits,
)


def _git_executable() -> str:
    git_executable = shutil.which("git")
    if git_executable is None:
        pytest.skip("git executable not available in PATH")
    return git_executable


def test_collect_fix_commits_parses_subjects_and_files_from_git_history(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_executable = _git_executable()

    _ = subprocess.run(
        [git_executable, "init"], cwd=repo, check=True, capture_output=True, text=True
    )
    _ = subprocess.run(
        [git_executable, "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    _ = subprocess.run(
        [git_executable, "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    target = repo / "src"
    target.mkdir()
    file_path = target / "module.py"
    file_path.write_text("print('v1')\n", encoding="utf-8")
    _ = subprocess.run(
        [git_executable, "add", "."],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    _ = subprocess.run(
        [git_executable, "commit", "-m", "feat(ui): seed module"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    file_path.write_text("print('v2')\n", encoding="utf-8")
    _ = subprocess.run(
        [git_executable, "add", "."],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    _ = subprocess.run(
        [git_executable, "commit", "-m", "fix(ui): repair session lifecycle race"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    commits = collect_fix_commits(repo)

    assert len(commits) == 1
    commit = commits[0]
    assert commit.subject == "fix(ui): repair session lifecycle race"
    assert commit.scope == "ui"
    assert commit.files == ["src/module.py"]


def test_build_phase1_artifacts_writes_expected_outputs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_executable = _git_executable()

    _ = subprocess.run(
        [git_executable, "init"], cwd=repo, check=True, capture_output=True, text=True
    )
    _ = subprocess.run(
        [git_executable, "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    _ = subprocess.run(
        [git_executable, "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    session_dir = repo / "src" / "coding_agent" / "ui"
    session_dir.mkdir(parents=True)
    session_file = session_dir / "session_manager.py"
    session_file.write_text("STATE = 'initial'\n", encoding="utf-8")
    _ = subprocess.run(
        [git_executable, "add", "."],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    _ = subprocess.run(
        [git_executable, "commit", "-m", "feat(ui): add session manager"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    session_file.write_text("STATE = 'stable'\n", encoding="utf-8")
    _ = subprocess.run(
        [git_executable, "add", "."],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    _ = subprocess.run(
        [
            git_executable,
            "commit",
            "-m",
            "fix(ui): stabilize session lifecycle transitions",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    storage_dir = repo / "src" / "agentkit" / "storage"
    storage_dir.mkdir(parents=True)
    storage_file = storage_dir / "pg.py"
    storage_file.write_text("POOL = None\n", encoding="utf-8")
    _ = subprocess.run(
        [git_executable, "add", "."],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    _ = subprocess.run(
        [
            git_executable,
            "commit",
            "-m",
            "fix(storage): validate pg config before startup",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    output_dir = repo / "postmortem"
    result = build_phase1_artifacts(repo, output_dir=output_dir)

    assert result.pattern_count == 2
    assert (output_dir / "README.md").exists()
    assert (output_dir / "taxonomy.yaml").exists()
    assert (output_dir / "index.yaml").exists()
    assert (output_dir / "onboarding" / "commit-classification-report.md").exists()
    assert (output_dir / "onboarding" / "historical-fix-clusters.md").exists()
    assert (output_dir / "onboarding" / "ingestion-log.md").exists()
    assert (output_dir / "templates" / "pattern.md").exists()
    assert (output_dir / "templates" / "release-risk-report.md").exists()

    index = yaml.safe_load((output_dir / "index.yaml").read_text(encoding="utf-8"))
    assert len(index["patterns"]) == 2
    assert {pattern["id"] for pattern in index["patterns"]} == {"PM-0001", "PM-0002"}
    assert any(
        "session lifecycle" in pattern["title"].lower() for pattern in index["patterns"]
    )
    assert any("pg config" in pattern["title"].lower() for pattern in index["patterns"])

    pattern_files = sorted((output_dir / "patterns").glob("PM-*.md"))
    assert len(pattern_files) == 2
    assert "Release Review Checklist" in pattern_files[0].read_text(encoding="utf-8")

    taxonomy = yaml.safe_load(
        (output_dir / "taxonomy.yaml").read_text(encoding="utf-8")
    )
    emitted_subsystems = {
        subsystem
        for pattern in index["patterns"]
        for subsystem in pattern["subsystems"]
    }
    assert emitted_subsystems <= set(taxonomy["subsystem"])

    ingestion_log = (output_dir / "onboarding" / "ingestion-log.md").read_text(
        encoding="utf-8"
    )
    assert f"Repository: `{repo.name}`" in ingestion_log
    assert "Output directory: `postmortem`" in ingestion_log
    assert str(repo) not in ingestion_log


def test_collect_fix_commits_scopes_history_to_requested_repo_subtree(
    tmp_path: Path,
) -> None:
    monorepo = tmp_path / "mono"
    monorepo.mkdir()

    import subprocess

    _ = subprocess.run(
        ["git", "init"], cwd=monorepo, check=True, capture_output=True, text=True
    )
    _ = subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=monorepo,
        check=True,
        capture_output=True,
        text=True,
    )
    _ = subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=monorepo,
        check=True,
        capture_output=True,
        text=True,
    )

    other_file = monorepo / "legacy" / "old.go"
    other_file.parent.mkdir(parents=True)
    other_file.write_text("package legacy\n", encoding="utf-8")
    _ = subprocess.run(
        ["git", "add", "."], cwd=monorepo, check=True, capture_output=True, text=True
    )
    _ = subprocess.run(
        ["git", "commit", "-m", "fix(runtime): patch legacy loop"],
        cwd=monorepo,
        check=True,
        capture_output=True,
        text=True,
    )

    app_repo = monorepo / "coding-agent"
    app_file = app_repo / "src" / "coding_agent" / "ui" / "session_manager.py"
    app_file.parent.mkdir(parents=True)
    app_file.write_text("STATE = 'draft'\n", encoding="utf-8")
    _ = subprocess.run(
        ["git", "add", "."], cwd=monorepo, check=True, capture_output=True, text=True
    )
    _ = subprocess.run(
        ["git", "commit", "-m", "fix(ui): stabilize session lifecycle transitions"],
        cwd=monorepo,
        check=True,
        capture_output=True,
        text=True,
    )

    commits = collect_fix_commits(app_repo)

    assert len(commits) == 1
    assert commits[0].subject == "fix(ui): stabilize session lifecycle transitions"
    assert commits[0].files == ["src/coding_agent/ui/session_manager.py"]


def test_build_phase1_artifacts_groups_related_review_fixes_into_one_pattern(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    import subprocess

    _ = subprocess.run(
        ["git", "init"], cwd=repo, check=True, capture_output=True, text=True
    )
    _ = subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    _ = subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    target = repo / "src" / "coding_agent" / "cli"
    target.mkdir(parents=True)
    command_file = target / "commands.py"
    command_file.write_text("STATE = 'base'\n", encoding="utf-8")
    _ = subprocess.run(
        ["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True
    )
    _ = subprocess.run(
        ["git", "commit", "-m", "feat(cli): add commands"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    command_file.write_text("STATE = 'review-a'\n", encoding="utf-8")
    _ = subprocess.run(
        ["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True
    )
    _ = subprocess.run(
        ["git", "commit", "-m", "fix(cli): address code review issues"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    command_file.write_text("STATE = 'review-b'\n", encoding="utf-8")
    _ = subprocess.run(
        ["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True
    )
    _ = subprocess.run(
        ["git", "commit", "-m", "fix(cli): address PR review comments"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    output_dir = repo / "postmortem"
    result = build_phase1_artifacts(repo, output_dir=output_dir)

    assert result.pattern_count == 1
    index = yaml.safe_load((output_dir / "index.yaml").read_text(encoding="utf-8"))
    assert index["patterns"][0]["related_commits"]
    assert len(index["patterns"][0]["related_commits"]) == 2


def test_build_phase1_artifacts_uses_paths_relative_to_output_root(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    import subprocess

    _ = subprocess.run(
        ["git", "init"], cwd=repo, check=True, capture_output=True, text=True
    )
    _ = subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    _ = subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    target = repo / "src" / "coding_agent" / "adapter.py"
    target.parent.mkdir(parents=True)
    target.write_text("STATE = 'base'\n", encoding="utf-8")
    _ = subprocess.run(
        ["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True
    )
    _ = subprocess.run(
        ["git", "commit", "-m", "feat(adapter): add adapter"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    target.write_text("STATE = 'fixed'\n", encoding="utf-8")
    _ = subprocess.run(
        ["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True
    )
    _ = subprocess.run(
        ["git", "commit", "-m", "fix(adapter): handle tool results safely"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    output_dir = repo / "custom-output"
    _ = build_phase1_artifacts(repo, output_dir=output_dir)
    index = yaml.safe_load((output_dir / "index.yaml").read_text(encoding="utf-8"))

    assert index["patterns"][0]["path"].startswith("patterns/")
