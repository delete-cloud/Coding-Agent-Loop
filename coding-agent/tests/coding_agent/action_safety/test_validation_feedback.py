from __future__ import annotations

import json

from coding_agent.action_safety import (
    CommandPolicyDecision,
    CommandPolicyVerdict,
    ValidationCommandSpec,
    ValidationOutcome,
    ValidationReport,
    ValidationRunner,
    ValidationStatus,
    render_validation_feedback_messages,
    validation_feedback_context_pack,
)


def test_validation_feedback_integrates_as_reference_context_without_raw_output(
    tmp_path,
) -> None:
    report = ValidationRunner().run(
        [
            ValidationCommandSpec(
                label="python -c 'print(SECRET_VALUE)'",
                command=(
                    'python -c "import sys\n'
                    "print('SECRET_VALUE')\n"
                    "sys.stderr.write('raw command output')\n"
                    'raise SystemExit(3)"'
                ),
            )
        ],
        workspace_root=tmp_path,
    )

    pack = validation_feedback_context_pack(report)
    messages = render_validation_feedback_messages(report)

    assert len(pack.sections) == 1
    item = pack.sections[0].items[0]
    assert item.source_kind == "runtime_hint"
    assert item.evidence[0].kind == "validation"
    assert item.evidence[0].command_label is None
    assert item.metadata["status"] == "failed"
    rendered = messages[0]["content"]
    serialized = json.dumps(pack.to_dict(), sort_keys=True) + str(rendered)
    assert "[Context Pack] Reference grounding for this turn." in str(rendered)
    assert "## Validation feedback" in str(rendered)
    assert "Exit code: 3." in str(rendered)
    assert "stdout_bytes=" in str(rendered)
    assert "stderr_bytes=" in str(rendered)
    assert "SECRET_VALUE" not in serialized
    assert "raw command output" not in serialized
    assert "python -c" not in serialized


def test_validation_feedback_omits_passed_outcomes(tmp_path) -> None:
    report = ValidationRunner().run(
        [
            ValidationCommandSpec(
                label="unit-pass",
                command="python -c \"print('ok')\"",
            )
        ],
        workspace_root=tmp_path,
    )

    assert validation_feedback_context_pack(report).sections == ()
    assert render_validation_feedback_messages(report) == []


def test_validation_feedback_omits_safe_looking_secret_labels(tmp_path) -> None:
    report = ValidationRunner().run(
        [
            ValidationCommandSpec(
                label="AWS_SECRET_ACCESS_KEY",
                command='python -c "raise SystemExit(2)"',
            )
        ],
        workspace_root=tmp_path,
    )

    pack = validation_feedback_context_pack(report)
    serialized = json.dumps(pack.to_dict(), sort_keys=True)

    assert pack.sections[0].items[0].evidence[0].command_label is None
    assert "AWS_SECRET_ACCESS_KEY" not in serialized


def test_validation_feedback_ignores_string_values_for_numeric_summary_fields() -> None:
    report = ValidationReport(
        status=ValidationStatus.FAILED,
        outcomes=(
            ValidationOutcome(
                label="unit",
                status=ValidationStatus.FAILED,
                exit_code=1,
                duration_ms=3,
                policy=CommandPolicyVerdict(
                    decision=CommandPolicyDecision.ALLOW,
                    reasons=(),
                    command_name="python",
                    environment_kind="local",
                    timeout_seconds=120,
                ),
                expected_success="exit_code",
                expected_exit_code=0,
                failure_summary={
                    "stdout_bytes": "raw stderr SECRET=abc",
                    "stderr_lines": 2,
                    "error_kind": "TimeoutExpired",
                },
            ),
        ),
    )

    rendered = render_validation_feedback_messages(report)[0]["content"]

    assert "stderr_lines=2" in str(rendered)
    assert "error_kind=TimeoutExpired" in str(rendered)
    assert "raw stderr" not in str(rendered)
    assert "SECRET=abc" not in str(rendered)
