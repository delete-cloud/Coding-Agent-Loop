from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from coding_agent.__main__ import main


def test_postmortem_phase1_command_generates_postmortem_directory(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    import subprocess

    subprocess.run(
        ["git", "init"], cwd=repo, check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    session_dir = repo / "src" / "coding_agent" / "ui"
    session_dir.mkdir(parents=True)
    session_file = session_dir / "http_server.py"
    session_file.write_text("STATE = 'draft'\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "commit", "-m", "feat(ui): add http server"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    session_file.write_text("STATE = 'stable'\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "commit", "-m", "fix(ui): harden http session transitions"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "postmortem",
            "phase1",
            "--repo",
            str(repo),
            "--output-dir",
            "postmortem",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "Generated Phase 1 postmortem onboarding artifacts" in result.output
    assert (repo / "postmortem" / "README.md").exists()
    assert (repo / "postmortem" / "index.yaml").exists()
