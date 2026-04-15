from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from coding_agent.__main__ import main


def _write_task_packet(path: Path, *, commands: list[str]) -> None:
    _ = path.write_text(
        """
Goal:
- Verify a bounded task packet

Target tests:
"""
        + "\n".join(f"- {command}" for command in commands)
        + "\n",
        encoding="utf-8",
    )


class TestVerifyCommand:
    def test_verify_checklist_prints_human_readable_output(
        self, tmp_path: Path
    ) -> None:
        packet = tmp_path / "task-packet.md"
        _ = _write_task_packet(packet, commands=["python3 -c \"print('ok')\""])

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["verify", "--task-packet", str(packet), "--mode", "checklist"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert "Verification Checklist" in result.output
        assert "Target test 1" in result.output
        assert "python3 -c \"print('ok')\"" in result.output

    def test_verify_run_prints_verified_for_passing_commands(
        self, tmp_path: Path
    ) -> None:
        packet = tmp_path / "task-packet.md"
        _ = _write_task_packet(packet, commands=["python3 -c \"print('ok')\""])

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["verify", "--task-packet", str(packet), "--mode", "run"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert "VERIFIED" in result.output
        assert "Target test 1" in result.output

    def test_verify_run_prints_not_verified_for_failing_commands(
        self, tmp_path: Path
    ) -> None:
        packet = tmp_path / "task-packet.md"
        _ = _write_task_packet(
            packet, commands=['python3 -c "import sys; sys.exit(5)"']
        )

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["verify", "--task-packet", str(packet), "--mode", "run"],
            catch_exceptions=False,
        )

        assert result.exit_code == 1
        assert "NOT VERIFIED" in result.output

    def test_verify_requires_task_packet_argument(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["verify"])

        assert result.exit_code != 0
