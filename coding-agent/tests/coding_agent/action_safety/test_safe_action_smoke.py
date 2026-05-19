from __future__ import annotations

import json
from pathlib import Path
import textwrap

from agentkit.observability import ObservationEvent, SpanRecord
from coding_agent.action_safety import (
    ACTION_OBSERVATION_NAME,
    ActionKind,
    ActionObservation,
    ActionObservationStatus,
    ActionApprovalRoute,
    PatchRiskLevel,
    ValidationCommandSpec,
    ValidationRunner,
    ValidationStatus,
    build_patch_plan,
    create_workspace_snapshot,
    evaluate_command_policy,
    record_action_span,
    restore_workspace_snapshot,
    route_command_action,
    route_file_patch_action,
    validate_safe_edit_path,
)
from coding_agent.tools.file_patch_tool import build_file_patch_tool


class InMemoryObservationSink:
    def __init__(self) -> None:
        self.spans: list[SpanRecord] = []
        self.events: list[ObservationEvent] = []

    def record_span(self, span: SpanRecord) -> None:
        self.spans.append(span)

    def record_event(self, event: ObservationEvent) -> None:
        self.events.append(event)


def test_safe_action_smoke_covers_patch_command_validation_and_restore(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    source_dir = workspace / "pkg"
    source_dir.mkdir(parents=True)
    (workspace / ".git").mkdir()
    _ = (workspace / ".git" / "HEAD").write_text(
        "ref: refs/heads/main\n",
        encoding="utf-8",
    )
    target = source_dir / "app.py"
    _ = target.write_text(
        "def value():\n    return 'before'\n",
        encoding="utf-8",
    )
    snapshot = create_workspace_snapshot(workspace, tmp_path / "snapshot")
    sink = InMemoryObservationSink()

    patch = textwrap.dedent(
        """\
        @@ -1,2 +1,2 @@
         def value():
        -    return 'before'
        +    return 'after'
        """
    )
    safe_edit_decision = validate_safe_edit_path(workspace, "pkg/app.py")
    patch_plan = build_patch_plan("pkg/app.py", patch, file_exists=True)
    patch_route = route_file_patch_action(patch_plan, safe_edit_decision)

    assert safe_edit_decision.allowed is True
    assert patch_plan.risk_level == PatchRiskLevel.LOW
    assert patch_route.route == ActionApprovalRoute.ALLOW

    patch_tool = build_file_patch_tool(workspace)
    dry_run_payload = json.loads(patch_tool("pkg/app.py", patch, dry_run=True))

    assert dry_run_payload["success"] is True
    assert dry_run_payload["dry_run"] is True
    assert target.read_text(encoding="utf-8") == "def value():\n    return 'before'\n"

    with record_action_span(
        sink,
        ActionObservation(
            kind=ActionKind.PATCH,
            status=ActionObservationStatus.STARTED,
            policy_decision=patch_route.route.value,
            policy_reasons=tuple(reason.value for reason in patch_route.reasons),
            risk_level=patch_plan.risk_level.value,
            changed_path_count=1,
            file_extension_buckets=("py",),
            dry_run=True,
        ),
    ) as patch_span:
        apply_payload = json.loads(patch_tool("pkg/app.py", patch))
        patch_span.set_observation(
            ActionObservation(
                kind=ActionKind.PATCH,
                status=ActionObservationStatus.COMPLETED,
                policy_decision=patch_route.route.value,
                policy_reasons=tuple(reason.value for reason in patch_route.reasons),
                risk_level=patch_plan.risk_level.value,
                changed_path_count=1,
                file_extension_buckets=("py",),
                dry_run=False,
            )
        )

    assert apply_payload["success"] is True
    assert target.read_text(encoding="utf-8") == "def value():\n    return 'after'\n"

    validation_command = "python -c pass"
    command_route = route_command_action(
        evaluate_command_policy(
            validation_command,
            environment_kind="local",
            workspace_root=workspace,
            cwd=workspace,
            validation_command=True,
        )
    )
    approval_route = route_command_action(
        evaluate_command_policy(
            "rm -rf build",
            environment_kind="local",
            workspace_root=workspace,
            cwd=workspace,
        )
    )

    assert command_route.route == ActionApprovalRoute.ALLOW
    assert approval_route.route == ActionApprovalRoute.APPROVAL_REQUIRED

    validation_report = ValidationRunner().run(
        [
            ValidationCommandSpec(
                label="unit_smoke",
                command=validation_command,
                cwd=workspace,
            )
        ],
        workspace_root=workspace,
    )

    assert validation_report.status == ValidationStatus.PASSED
    assert validation_report.outcomes[0].exit_code == 0

    with record_action_span(
        sink,
        ActionObservation(
            kind=ActionKind.VALIDATION,
            status=ActionObservationStatus.STARTED,
            policy_decision=command_route.route.value,
            policy_reasons=tuple(reason.value for reason in command_route.reasons),
            command_label="unit_smoke",
        ),
    ) as validation_span:
        validation_span.set_observation(
            ActionObservation(
                kind=ActionKind.VALIDATION,
                status=ActionObservationStatus.COMPLETED,
                policy_decision=command_route.route.value,
                policy_reasons=tuple(reason.value for reason in command_route.reasons),
                command_label="unit_smoke",
                exit_code=validation_report.outcomes[0].exit_code,
                duration_ms=validation_report.outcomes[0].duration_ms,
            )
        )

    _ = target.write_text("broken\n", encoding="utf-8")
    _ = (workspace / "stale.txt").write_text("remove me\n", encoding="utf-8")
    _ = (workspace / ".git" / "HEAD").write_text(
        "ref: refs/heads/feature\n",
        encoding="utf-8",
    )

    with record_action_span(
        sink,
        ActionObservation(
            kind=ActionKind.RESTORE,
            status=ActionObservationStatus.STARTED,
            restore_status="started",
        ),
    ) as restore_span:
        restore_workspace_snapshot(snapshot, workspace)
        restore_span.set_observation(
            ActionObservation(
                kind=ActionKind.RESTORE,
                status=ActionObservationStatus.COMPLETED,
                restore_status="completed",
                changed_path_count=snapshot.file_count,
            )
        )

    assert target.read_text(encoding="utf-8") == "def value():\n    return 'before'\n"
    assert not (workspace / "stale.txt").exists()
    assert (workspace / ".git" / "HEAD").read_text(
        encoding="utf-8"
    ) == "ref: refs/heads/feature\n"
    assert [span.name for span in sink.spans] == [
        ACTION_OBSERVATION_NAME,
        ACTION_OBSERVATION_NAME,
        ACTION_OBSERVATION_NAME,
    ]

    serialized_safe_payloads = json.dumps(
        {
            "patch_route": patch_route.to_safe_dict(),
            "command_route": command_route.to_safe_dict(),
            "approval_route": approval_route.to_safe_dict(),
            "validation_report": validation_report.to_safe_dict(),
            "spans": [span.attributes for span in sink.spans],
        },
        sort_keys=True,
    )
    assert "return 'after'" not in serialized_safe_payloads
    assert "return 'before'" not in serialized_safe_payloads
    assert "rm -rf" not in serialized_safe_payloads
