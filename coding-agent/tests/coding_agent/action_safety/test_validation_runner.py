from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent.action_safety import (
    ValidationCommandSpec,
    ValidationRunner,
    ValidationStatus,
)


def test_validation_runner_records_structured_outcomes_for_deterministic_commands(
    tmp_path: Path,
) -> None:
    runner = ValidationRunner()
    specs = [
        ValidationCommandSpec(
            label="unit pass",
            command="python -c \"print('ok')\"",
        ),
        ValidationCommandSpec(
            label="unit fail",
            command='python -c "raise SystemExit(3)"',
            expected_exit_code=0,
        ),
    ]

    report = runner.run(specs, workspace_root=tmp_path)

    assert report.status == ValidationStatus.FAILED
    assert [outcome.label for outcome in report.outcomes] == ["unit pass", "unit fail"]
    assert report.outcomes[0].status == ValidationStatus.PASSED
    assert report.outcomes[0].exit_code == 0
    assert report.outcomes[0].failure_summary == {}
    assert report.outcomes[1].status == ValidationStatus.FAILED
    assert report.outcomes[1].exit_code == 3
    assert report.outcomes[1].failure_summary == {
        "stdout_bytes": 0,
        "stderr_bytes": 0,
        "stdout_lines": 0,
        "stderr_lines": 0,
    }

    payload = report.to_safe_dict()
    serialized = json.dumps(payload, sort_keys=True)
    assert payload["outcomes"][0]["policy"]["decision"] == "allow"
    assert "print('ok')" not in serialized
    assert "raise SystemExit" not in serialized


def test_validation_runner_does_not_execute_denied_or_approval_commands(
    tmp_path: Path,
) -> None:
    runner = ValidationRunner()
    marker = tmp_path / "marker"
    specs = [
        ValidationCommandSpec(
            label="shell syntax",
            command=f"python -c \"print('x')\" > {marker}",
        ),
        ValidationCommandSpec(
            label="destructive",
            command="rm -rf build",
        ),
    ]

    report = runner.run(specs, workspace_root=tmp_path)

    assert [outcome.status for outcome in report.outcomes] == [
        ValidationStatus.DENIED,
        ValidationStatus.APPROVAL_REQUIRED,
    ]
    assert not marker.exists()
    assert report.outcomes[0].exit_code is None
    assert report.outcomes[1].exit_code is None


def test_validation_runner_rejects_cloud_execution() -> None:
    runner = ValidationRunner()

    with pytest.raises(ValueError, match="local execution"):
        _ = runner.run(
            [ValidationCommandSpec(label="unit", command="python -c pass")],
            workspace_root=Path("/workspace"),
            environment_kind="cloud",
        )


def test_validation_runner_does_not_inherit_parent_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODING_AGENT_SECRET_TOKEN", "not-inherited")
    runner = ValidationRunner()

    report = runner.run(
        [
            ValidationCommandSpec(
                label="env isolation",
                command=(
                    'python -c "import os\nimport sys\n'
                    "sys.exit(7 if 'CODING_AGENT_SECRET_TOKEN' in os.environ else 0)\""
                ),
            )
        ],
        workspace_root=tmp_path,
    )

    assert report.status == ValidationStatus.PASSED
    assert report.outcomes[0].exit_code == 0


def test_validation_runner_rejects_empty_spec_list(tmp_path: Path) -> None:
    runner = ValidationRunner()

    with pytest.raises(ValueError, match="at least one"):
        _ = runner.run([], workspace_root=tmp_path)


def test_validation_command_spec_rejects_missing_contract_fields() -> None:
    with pytest.raises(ValueError, match="label"):
        _ = ValidationCommandSpec(label="", command="python -c pass")

    with pytest.raises(ValueError, match="command"):
        _ = ValidationCommandSpec(label="unit", command="")

    with pytest.raises(ValueError, match="timeout"):
        _ = ValidationCommandSpec(
            label="unit",
            command="python -c pass",
            timeout_seconds=0,
        )
