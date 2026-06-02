from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from datetime import UTC, datetime
import subprocess
import sys

from click.testing import CliRunner
import pytest

from agentkit.checkpoint.models import CheckpointMeta
from coding_agent.__main__ import main
from coding_agent.wire.protocol import CompletionStatus, StreamDelta, TurnEnd


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
    for command in (
        "daemon",
        "run",
        "repl",
        "resume",
        "sessions",
        "serve",
        "storage",
        "verify",
    ):
        assert command in completed.stdout


def test_subcommand_help_is_available_without_provider_credentials() -> None:
    runner = CliRunner(env=_click_credential_free_env())

    for command in (
        "daemon",
        "run",
        "repl",
        "resume",
        "sessions",
        "serve",
        "storage",
        "verify",
    ):
        result = runner.invoke(main, [command, "--help"], catch_exceptions=False)

        assert result.exit_code == 0
        assert "Usage:" in result.output
        assert command in result.output


def test_default_non_interactive_entrypoint_points_to_one_shot_compatibility() -> None:
    runner = CliRunner(env=_click_credential_free_env())

    result = runner.invoke(main, [])

    assert result.exit_code != 0
    assert "interactive REPL mode requires an interactive terminal" in result.output
    assert "python -m coding_agent repl" in result.output
    assert "dev/testkit one-shot compatibility session" in result.output


def test_run_help_marks_command_as_dev_testkit_compatibility() -> None:
    runner = CliRunner(env=_click_credential_free_env())

    result = runner.invoke(main, ["run", "--help"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "dev/testkit one-shot local session" in result.output
    assert "compatibility path" in result.output


def test_run_command_uses_managed_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coding_agent.cli import main as cli_main
    from coding_agent.wire.local import LocalWire

    calls: list[tuple[str, object]] = []
    wire = LocalWire("session-managed")

    async def fake_get_next_outgoing():
        if not hasattr(fake_get_next_outgoing, "count"):
            fake_get_next_outgoing.count = 0  # type: ignore[attr-defined]
        fake_get_next_outgoing.count += 1  # type: ignore[attr-defined]
        if fake_get_next_outgoing.count == 1:  # type: ignore[attr-defined]
            return StreamDelta(
                session_id="session-managed",
                agent_id="",
                content="managed output",
            )
        return TurnEnd(
            session_id="session-managed",
            agent_id="",
            turn_id="run-managed",
            completion_status=CompletionStatus.COMPLETED,
        )

    monkeypatch.setattr(wire, "get_next_outgoing", fake_get_next_outgoing)

    class FakeSessionManager:
        def __init__(self, *args, **kwargs) -> None:
            calls.append(("init", {"args": args, "kwargs": kwargs}))

        async def create_session(self, **kwargs):
            calls.append(("create_session", kwargs))
            return "session-managed"

        async def get_session_async(self, session_id: str):
            calls.append(("get_session_async", session_id))
            return SimpleNamespace(wire=wire)

        async def run_agent(self, session_id: str, prompt: str) -> None:
            calls.append(("run_agent", {"session_id": session_id, "prompt": prompt}))

        async def close(self) -> None:
            calls.append(("close", None))

    monkeypatch.setattr(
        cli_main, "create_local_cli_session_manager", FakeSessionManager
    )
    runner = CliRunner(env=_click_credential_free_env())

    result = runner.invoke(
        main,
        [
            "run",
            "--goal",
            "use the one-shot compatibility path",
            "--repo",
            str(tmp_path),
            "--max-steps",
            "3",
            "--approval",
            "yolo",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "managed output" in result.output
    assert calls[0] == (
        "init",
        {
            "args": (),
            "kwargs": {
                "storage_config": {
                    "http_session_backend": "fs",
                    "runtime_backend": "jsonl",
                }
            },
        },
    )
    assert calls == [
        calls[0],
        (
            "create_session",
            {
                "repo_path": tmp_path,
                "approval_policy": cli_main.ApprovalPolicy.YOLO,
                "provider_name": "openai",
                "model_name": "gpt-4o",
                "base_url": None,
                "max_steps": 3,
            },
        ),
        ("get_session_async", "session-managed"),
        (
            "run_agent",
            {
                "session_id": "session-managed",
                "prompt": "use the one-shot compatibility path",
            },
        ),
        ("close", None),
    ]


def test_run_patch_mode_augments_goal_and_requires_worktree_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coding_agent.cli import main as cli_main

    snapshots = iter(
        [
            cli_main.WorktreeSnapshot(status="", diff="before"),
            cli_main.WorktreeSnapshot(status=" M changed.py", diff="after"),
        ]
    )
    verify_calls: list[tuple[Path, tuple[str, ...]]] = []

    async def fake_run_headless(config, goal: str) -> None:
        assert Path(config.repo) == tmp_path
        assert "implement the task" in goal
        assert "Patch-oriented run contract:" in goal
        assert "uv run pytest tests/cli/test_entrypoint_contract.py -v" in goal

    def fake_capture(repo_root: Path) -> object:
        assert repo_root == tmp_path
        return next(snapshots)

    def fake_verify(repo_root: Path, commands: tuple[str, ...]) -> None:
        verify_calls.append((repo_root, commands))

    monkeypatch.setattr(cli_main, "_run_headless", fake_run_headless)
    monkeypatch.setattr(cli_main, "_capture_worktree_snapshot", fake_capture)
    monkeypatch.setattr(cli_main, "_run_post_run_verification", fake_verify)

    runner = CliRunner(env=_click_credential_free_env())
    result = runner.invoke(
        main,
        [
            "run",
            "--goal",
            "implement the task",
            "--repo",
            str(tmp_path),
            "--patch",
            "--verify-cmd",
            "uv run pytest tests/cli/test_entrypoint_contract.py -v",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert verify_calls == [
        (
            tmp_path,
            ("uv run pytest tests/cli/test_entrypoint_contract.py -v",),
        )
    ]


def test_run_patch_mode_fails_when_agent_produces_no_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coding_agent.cli import main as cli_main

    snapshot = cli_main.WorktreeSnapshot(status="", diff="")
    run_calls: list[str] = []

    async def fake_run_headless(config, goal: str) -> None:
        del config
        run_calls.append(goal)

    monkeypatch.setattr(cli_main, "_run_headless", fake_run_headless)
    monkeypatch.setattr(cli_main, "_capture_worktree_snapshot", lambda repo: snapshot)

    runner = CliRunner(env=_click_credential_free_env())
    result = runner.invoke(
        main,
        [
            "run",
            "--goal",
            "only plan",
            "--repo",
            str(tmp_path),
            "--patch",
        ],
    )

    assert result.exit_code != 0
    assert run_calls
    assert "patch run produced no repository changes" in result.output


def test_resume_command_uses_managed_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coding_agent.cli import main as cli_main
    from coding_agent.wire.local import LocalWire

    calls: list[tuple[str, object]] = []
    wire = LocalWire("session-managed")

    async def fake_get_next_outgoing():
        return TurnEnd(
            session_id="session-managed",
            agent_id="",
            turn_id="run-resumed",
            completion_status=CompletionStatus.COMPLETED,
        )

    monkeypatch.setattr(wire, "get_next_outgoing", fake_get_next_outgoing)

    class FakeSessionManager:
        def __init__(self, *args, **kwargs) -> None:
            calls.append(("init", {"args": args, "kwargs": kwargs}))

        async def get_session_async(self, session_id: str):
            calls.append(("get_session_async", session_id))
            return SimpleNamespace(wire=wire)

        async def resume_session(self, session_id: str, **kwargs):
            calls.append(("resume_session", {"session_id": session_id, **kwargs}))
            return SimpleNamespace(run_id="run-resumed")

        async def close(self) -> None:
            calls.append(("close", None))

    monkeypatch.setattr(
        cli_main, "create_local_cli_session_manager", FakeSessionManager
    )
    runner = CliRunner(env=_click_credential_free_env())

    result = runner.invoke(
        main,
        [
            "resume",
            "--session",
            "session-managed",
            "--prompt",
            "continue",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert calls[0] == (
        "init",
        {
            "args": (),
            "kwargs": {
                "storage_config": {
                    "http_session_backend": "fs",
                    "runtime_backend": "jsonl",
                }
            },
        },
    )
    assert calls == [
        calls[0],
        ("get_session_async", "session-managed"),
        (
            "resume_session",
            {
                "session_id": "session-managed",
                "prompt": "continue",
                "resume_reason": "local_cli_resume",
            },
        ),
        ("close", None),
    ]


def test_local_sessions_list_reports_resume_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coding_agent.cli import main as cli_main

    calls: list[tuple[str, object]] = []

    class FakeSession:
        id = "session-local"
        last_activity = "2026-06-01T10:00:00+00:00"

        def as_dict(self) -> dict[str, object]:
            return {
                "session_id": self.id,
                "status": "created",
                "turn_status": "idle",
                "workspace_id": None,
                "last_activity": self.last_activity,
            }

    class FakeSessionManager:
        def __init__(self, *args, **kwargs) -> None:
            calls.append(("init", {"args": args, "kwargs": kwargs}))

        async def list_sessions_async(self):
            calls.append(("list_sessions_async", None))
            return ["session-local"]

        async def get_session_async(self, session_id: str):
            calls.append(("get_session_async", session_id))
            return FakeSession()

        async def session_resume_metadata(self, session_id: str):
            calls.append(("session_resume_metadata", session_id))
            return {
                "resumable": True,
                "last_run_id": "run-2",
                "last_run_status": "interrupted",
                "last_interrupted_run_id": "run-2",
                "resume_from_event_id": "event-9",
                "checkpoint_count": 1,
                "latest_checkpoint_id": "cp-1",
                "latest_checkpoint_label": "latest",
            }

        async def close(self) -> None:
            calls.append(("close", None))

    monkeypatch.setattr(
        cli_main, "create_local_cli_session_manager", FakeSessionManager
    )
    runner = CliRunner(env=_click_credential_free_env())

    result = runner.invoke(
        main,
        ["sessions", "list"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert result.output.strip().split("\t") == [
        "session-local",
        "created",
        "idle",
        "None",
        "interrupted",
        "resumable",
        "run-2",
        "cp-1",
    ]
    assert calls == [
        calls[0],
        ("list_sessions_async", None),
        ("get_session_async", "session-local"),
        ("session_resume_metadata", "session-local"),
        ("close", None),
    ]
    assert calls[0] == (
        "init",
        {
            "args": (),
            "kwargs": {
                "storage_config": {
                    "http_session_backend": "fs",
                    "runtime_backend": "jsonl",
                }
            },
        },
    )


def test_local_session_status_reports_checkpoint_and_resume_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coding_agent.cli import main as cli_main

    class FakeSession:
        id = "session-local"

        def as_dict(self) -> dict[str, object]:
            return {
                "session_id": self.id,
                "status": "created",
                "turn_status": "idle",
            }

    class FakeSessionManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def get_session_async(self, session_id: str):
            assert session_id == "session-local"
            return FakeSession()

        async def session_resume_metadata(self, session_id: str):
            assert session_id == "session-local"
            return {
                "resumable": True,
                "last_run_id": "run-2",
                "last_run_status": "interrupted",
                "last_interrupted_run_id": "run-2",
                "resume_from_event_id": "event-9",
                "checkpoint_count": 2,
                "latest_checkpoint_id": "cp-2",
                "latest_checkpoint_label": "latest",
            }

        async def close(self) -> None:
            pass

    monkeypatch.setattr(
        cli_main, "create_local_cli_session_manager", FakeSessionManager
    )
    runner = CliRunner(env=_click_credential_free_env())

    result = runner.invoke(
        main,
        ["session", "session-local"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "session_id: session-local" in result.output
    assert "last_run_status: interrupted" in result.output
    assert "last_interrupted_run_id: run-2" in result.output
    assert "checkpoint_count: 2" in result.output
    assert "latest_checkpoint_id: cp-2" in result.output
    assert "resumable: True" in result.output


def test_local_sessions_checkpoints_lists_newest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coding_agent.cli import main as cli_main

    calls: list[tuple[str, object]] = []

    class FakeSessionManager:
        def __init__(self, *args, **kwargs) -> None:
            calls.append(("init", {"args": args, "kwargs": kwargs}))

        async def list_checkpoints(self, session_id: str):
            calls.append(("list_checkpoints", session_id))
            return [
                CheckpointMeta(
                    checkpoint_id="cp-old",
                    tape_id="tape-local",
                    session_id=session_id,
                    entry_count=1,
                    window_start=0,
                    created_at=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
                    label="old",
                ),
                CheckpointMeta(
                    checkpoint_id="cp-new",
                    tape_id="tape-local",
                    session_id=session_id,
                    entry_count=3,
                    window_start=1,
                    created_at=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
                    label="new",
                ),
            ]

        async def close(self) -> None:
            calls.append(("close", None))

    monkeypatch.setattr(
        cli_main, "create_local_cli_session_manager", FakeSessionManager
    )
    runner = CliRunner(env=_click_credential_free_env())

    result = runner.invoke(
        main,
        ["sessions", "checkpoints", "session-local"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert lines[0].split("\t") == [
        "cp-new",
        "2026-06-01T10:00:00+00:00",
        "3",
        "1",
        "new",
    ]
    assert lines[1].split("\t") == [
        "cp-old",
        "2026-06-01T09:00:00+00:00",
        "1",
        "0",
        "old",
    ]
    assert calls == [
        calls[0],
        ("list_checkpoints", "session-local"),
        ("close", None),
    ]
    assert calls[0] == (
        "init",
        {
            "args": (),
            "kwargs": {
                "storage_config": {
                    "http_session_backend": "fs",
                    "runtime_backend": "jsonl",
                }
            },
        },
    )


def test_storage_migrate_sqlite_command_reports_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coding_agent.cli import main as cli_main
    from coding_agent.storage_migration import (
        LegacySQLiteMigrationReport,
        StoreMigrationReport,
    )

    calls: list[dict[str, object]] = []

    async def fake_migrate_legacy_storage_to_sqlite(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return LegacySQLiteMigrationReport(
            tapes=StoreMigrationReport(scanned=2, migrated=1, skipped=1),
            checkpoints=StoreMigrationReport(scanned=3, migrated=3, skipped=0),
        )

    monkeypatch.setattr(
        cli_main,
        "migrate_legacy_storage_to_sqlite",
        fake_migrate_legacy_storage_to_sqlite,
    )
    runner = CliRunner(env=_click_credential_free_env())

    result = runner.invoke(
        main,
        [
            "storage",
            "migrate-sqlite",
            "--data-dir",
            str(tmp_path),
            "--replace-tapes",
            "--dry-run",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "tapes: scanned=2 migrated=1 skipped=1" in result.output
    assert "checkpoints: scanned=3 migrated=3 skipped=0" in result.output
    assert calls == [
        {
            "args": (tmp_path,),
            "kwargs": {
                "tapes_dir": None,
                "checkpoints_dir": None,
                "tape_sqlite_path": None,
                "checkpoint_sqlite_path": None,
                "replace_tapes": True,
                "dry_run": True,
            },
        }
    ]


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
