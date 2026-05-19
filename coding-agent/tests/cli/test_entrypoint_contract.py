from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from click.testing import CliRunner

from coding_agent.__main__ import main


_CREDENTIAL_ENV_KEYS = (
    "AGENT_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "MOONSHOT_API_KEY",
    "KIMI_CODE_API_KEY",
    "DEEPSEEK_API_KEY",
    "GITHUB_TOKEN",
)


def test_module_help_lists_release_entrypoint_commands_without_credentials() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "coding_agent", "--help"],
        check=False,
        capture_output=True,
        text=True,
        cwd=Path.cwd(),
        env=_credential_free_env(),
        timeout=10,
    )

    assert completed.returncode == 0
    assert "Coding Agent CLI" in completed.stdout
    for command in ("run", "repl", "serve", "verify"):
        assert command in completed.stdout


def test_subcommand_help_is_available_without_provider_credentials() -> None:
    runner = CliRunner(env=_click_credential_free_env())

    for command in ("run", "repl", "serve", "verify"):
        result = runner.invoke(main, [command, "--help"], catch_exceptions=False)

        assert result.exit_code == 0
        assert "Usage:" in result.output
        assert command in result.output


def test_default_non_interactive_entrypoint_points_to_batch_mode() -> None:
    runner = CliRunner(env=_click_credential_free_env())

    result = runner.invoke(main, [])

    assert result.exit_code != 0
    assert "interactive REPL mode requires an interactive terminal" in result.output
    assert "python -m coding_agent run --goal" in result.output


def _credential_free_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in _CREDENTIAL_ENV_KEYS:
        env.pop(key, None)
    env["PYTHONPATH"] = str(Path("src").resolve())
    return env


def _click_credential_free_env() -> dict[str, str | None]:
    env: dict[str, str | None] = {key: None for key in _CREDENTIAL_ENV_KEYS}
    env["PYTHONPATH"] = str(Path("src").resolve())
    return env
