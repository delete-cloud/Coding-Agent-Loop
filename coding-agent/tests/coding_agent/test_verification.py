from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.verification import (
    ChecklistRenderResult,
    VerificationRunner,
    load_task_packet_contract,
)


def _write_task_packet(path: Path, *, commands: list[str]) -> None:
    path.write_text(
        """
Goal:
- Verify a bounded task packet

Target tests:
"""
        + "\n".join(f"- `{command}`" for command in commands)
        + "\n",
        encoding="utf-8",
    )


class TestTaskPacketVerificationContract:
    def test_load_task_packet_contract_reads_target_tests(self, tmp_path: Path) -> None:
        packet = tmp_path / "task-packet.md"
        _ = _write_task_packet(
            packet,
            commands=[
                "uv run pytest tests/cli/test_commands.py -v",
                "uv run pytest tests/coding_agent/test_pipeline_adapter.py -v",
            ],
        )

        contract = load_task_packet_contract(packet)

        assert contract.source_path == packet
        assert [step.name for step in contract.steps] == [
            "Target test 1",
            "Target test 2",
        ]
        assert [step.command for step in contract.steps] == [
            "uv run pytest tests/cli/test_commands.py -v",
            "uv run pytest tests/coding_agent/test_pipeline_adapter.py -v",
        ]

    def test_load_task_packet_contract_requires_target_tests(
        self, tmp_path: Path
    ) -> None:
        packet = tmp_path / "task-packet.md"
        packet.write_text("Goal:\n- Missing target tests\n", encoding="utf-8")

        with pytest.raises(ValueError, match="Target tests"):
            load_task_packet_contract(packet)


class TestVerificationRunner:
    def test_verify_run_executes_target_tests_from_task_packet(
        self, tmp_path: Path
    ) -> None:
        packet = tmp_path / "task-packet.md"
        _ = _write_task_packet(packet, commands=["python3 -c \"print('ok')\""])

        runner = VerificationRunner()
        report = runner.run(load_task_packet_contract(packet))

        assert report.verdict == "VERIFIED"
        assert len(report.steps) == 1
        assert report.steps[0].passed is True
        assert report.steps[0].exit_code == 0
        assert "ok" in report.steps[0].stdout

    def test_verify_run_reports_not_verified_when_command_fails(
        self, tmp_path: Path
    ) -> None:
        packet = tmp_path / "task-packet.md"
        _ = _write_task_packet(
            packet, commands=['python3 -c "import sys; sys.exit(3)"']
        )

        runner = VerificationRunner()
        report = runner.run(load_task_packet_contract(packet))

        assert report.verdict == "NOT VERIFIED"
        assert len(report.steps) == 1
        assert report.steps[0].passed is False
        assert report.steps[0].exit_code == 3

    def test_verify_checklist_renders_target_tests_from_task_packet(
        self, tmp_path: Path
    ) -> None:
        packet = tmp_path / "task-packet.md"
        _ = _write_task_packet(
            packet,
            commands=[
                "uv run pytest tests/cli/test_commands.py -v",
                "uv run pytest tests/coding_agent/test_pipeline_adapter.py -v",
            ],
        )

        runner = VerificationRunner()
        checklist = runner.render_checklist(load_task_packet_contract(packet))

        assert isinstance(checklist, ChecklistRenderResult)
        assert "Verification Checklist" in checklist.text
        assert "Target test 1" in checklist.text
        assert "Target test 2" in checklist.text
        assert "uv run pytest tests/cli/test_commands.py -v" in checklist.text
        assert (
            "uv run pytest tests/coding_agent/test_pipeline_adapter.py -v"
            in checklist.text
        )
