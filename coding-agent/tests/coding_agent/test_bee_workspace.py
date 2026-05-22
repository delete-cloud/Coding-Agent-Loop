from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent.bee_workspace import (
    BeeWorkspaceRunArtifacts,
    BeeWorkspaceRunNode,
    build_bee_manifest_from_workspace_template,
    discover_bee_workspace_run_artifacts,
    discover_bee_workspace_templates,
    load_bee_workspace_command_intents,
    load_bee_workspace_template,
    write_bee_workspace_run_artifacts,
)


def test_bee_workspace_discovers_template_metadata(tmp_path: Path) -> None:
    template_dir = tmp_path / ".bee" / "templates" / "template-alpha"
    feature_dir = template_dir / "features"
    feature_dir.mkdir(parents=True)
    (template_dir / "metadata.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "template_id: template-alpha",
                "kind: maintenance",
                "profile: local",
                "title: Local template alpha",
                "summary: Safe local template",
                "topic:",
                "  session_id: session-alpha",
                "context_profile: default",
                "validation_profile: smoke",
                "workspace_policy: local",
                "nodes:",
                "  - node_id: node-plan",
                "    kind: analysis",
                "    profile: default",
                "    title: Plan local task",
            ]
        ),
        encoding="utf-8",
    )
    (template_dir / "SKILL.md").write_text("# Template Skill\n", encoding="utf-8")
    (feature_dir / "acceptance.feature").write_text(
        "Feature: safe local template\n", encoding="utf-8"
    )
    (template_dir / "commands.yaml").write_text(
        "\n".join(
            [
                "commands:",
                "  - name: smoke",
                "    profile: validation",
                "    policy: existing_command_policy",
                "    category: validation",
                "    validation_label: pytest_smoke",
                "    metadata:",
                "      owner: local",
            ]
        ),
        encoding="utf-8",
    )

    templates = discover_bee_workspace_templates(tmp_path)

    assert [template.template_id for template in templates] == ["template-alpha"]
    template = templates[0]
    assert template.metadata_path == template_dir / "metadata.yaml"
    assert template.skill_path == template_dir / "SKILL.md"
    assert template.feature_paths == (feature_dir / "acceptance.feature",)
    assert template.commands_path == template_dir / "commands.yaml"
    assert template.metadata["kind"] == "maintenance"
    assert template.metadata["topic"] == {"session_id": "session-alpha"}


def test_bee_workspace_commands_yaml_is_non_executing_intent(
    tmp_path: Path,
) -> None:
    template_dir = tmp_path / ".bee" / "templates" / "template-alpha"
    _write_safe_template(template_dir, template_id="template-alpha")
    (template_dir / "commands.yaml").write_text(
        "\n".join(
            [
                "commands:",
                "  - name: smoke",
                "    profile: validation",
                "    policy: existing_command_policy",
                "    category: validation",
                "    validation_label: pytest_smoke",
                "    metadata:",
                "      owner: local",
            ]
        ),
        encoding="utf-8",
    )
    template = load_bee_workspace_template(tmp_path, "template-alpha")

    intents = load_bee_workspace_command_intents(template)

    assert len(intents) == 1
    assert intents[0].name == "smoke"
    assert intents[0].profile == "validation"
    assert intents[0].policy == "existing_command_policy"
    assert intents[0].category == "validation"
    assert intents[0].validation_label == "pytest_smoke"
    assert intents[0].metadata == {"owner": "local"}


def test_bee_workspace_commands_yaml_allows_safe_context_metadata(
    tmp_path: Path,
) -> None:
    template_dir = tmp_path / ".bee" / "templates" / "template-alpha"
    _write_safe_template(template_dir, template_id="template-alpha")
    (template_dir / "commands.yaml").write_text(
        "\n".join(
            [
                "commands:",
                "  - name: smoke",
                "    profile: validation",
                "    policy: existing_command_policy",
                "    category: validation",
                "    metadata:",
                "      contextProfile: default",
            ]
        ),
        encoding="utf-8",
    )
    template = load_bee_workspace_template(tmp_path, "template-alpha")

    intents = load_bee_workspace_command_intents(template)

    assert intents[0].metadata == {"contextProfile": "default"}


@pytest.mark.parametrize("field", ["command", "cmd", "shell", "script", "argv"])
def test_bee_workspace_commands_yaml_rejects_executable_fields(
    tmp_path: Path,
    field: str,
) -> None:
    template_dir = tmp_path / ".bee" / "templates" / "template-alpha"
    _write_safe_template(template_dir, template_id="template-alpha")
    (template_dir / "commands.yaml").write_text(
        "\n".join(
            [
                "commands:",
                "  - name: unsafe",
                "    profile: validation",
                "    policy: existing_command_policy",
                "    category: validation",
                f"    {field}: pytest",
            ]
        ),
        encoding="utf-8",
    )
    template = load_bee_workspace_template(tmp_path, "template-alpha")

    with pytest.raises(ValueError, match="forbidden sensitive field|not supported"):
        load_bee_workspace_command_intents(template)


@pytest.mark.parametrize(
    "metadata_key",
    [
        "metadata.command",
        "metadata.apiKey",
        "metadata.args",
        "metadata.commands",
        "metadata.accessKey",
        "metadata.bearer",
        "metadata.argv",
        "metadata.cmd",
        "metadata.shell",
        "metadata.script",
        "metadata.executor",
        "metadata.commandOutput",
        "metadata.credentials",
        "metadata.COMMANDOutput",
        "metadata.stdoutText",
        "metadata.STDOUTText",
        "metadata.promptText",
        "metadata.privateKey",
        "metadata.AWSSecretAccessKey",
        "metadata.secretToken",
        "metadata.environment",
    ],
)
def test_bee_workspace_commands_yaml_rejects_nested_sensitive_metadata_keys(
    tmp_path: Path,
    metadata_key: str,
) -> None:
    template_dir = tmp_path / ".bee" / "templates" / "template-alpha"
    _write_safe_template(template_dir, template_id="template-alpha")
    parent, child = metadata_key.split(".")
    (template_dir / "commands.yaml").write_text(
        "\n".join(
            [
                "commands:",
                "  - name: unsafe",
                "    profile: validation",
                "    policy: existing_command_policy",
                "    category: validation",
                f"    {parent}:",
                f"      {child}: local",
            ]
        ),
        encoding="utf-8",
    )
    template = load_bee_workspace_template(tmp_path, "template-alpha")

    with pytest.raises(ValueError, match="forbidden sensitive field"):
        load_bee_workspace_command_intents(template)


def test_bee_workspace_loads_json_metadata(tmp_path: Path) -> None:
    template_dir = tmp_path / ".bee" / "templates" / "json-template"
    feature_dir = template_dir / "features"
    feature_dir.mkdir(parents=True)
    (template_dir / "metadata.json").write_text(
        json.dumps(
            {
                "version": 1,
                "template_id": "json-template",
                "kind": "maintenance",
                "profile": "local",
                "title": "JSON template",
                "topic": {"session_id": "session-alpha"},
                "nodes": [
                    {
                        "node_id": "node-plan",
                        "kind": "analysis",
                        "profile": "default",
                        "title": "Plan JSON task",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (template_dir / "SKILL.md").write_text("# JSON Skill\n", encoding="utf-8")
    (feature_dir / "acceptance.feature").write_text(
        "Feature: JSON template\n", encoding="utf-8"
    )

    template = load_bee_workspace_template(tmp_path, "json-template")

    assert template.template_id == "json-template"
    assert template.metadata_path == template_dir / "metadata.json"
    assert template.commands_path is None


def test_bee_workspace_builds_manifest_with_existing_parser(tmp_path: Path) -> None:
    template_dir = tmp_path / ".bee" / "templates" / "template-alpha"
    _write_safe_template(template_dir, template_id="template-alpha")

    template = load_bee_workspace_template(tmp_path, "template-alpha")
    manifest = build_bee_manifest_from_workspace_template(template)

    assert manifest.kind == "maintenance"
    assert manifest.profile == "local"
    assert manifest.title == "Local template"
    assert manifest.topic.session_id == "session-alpha"
    assert manifest.nodes[0].node_id == "node-plan"
    assert manifest.metadata["template_id"] == "template-alpha"


def test_bee_workspace_writes_safe_task_json(tmp_path: Path) -> None:
    paths = write_bee_workspace_run_artifacts(
        tmp_path,
        BeeWorkspaceRunArtifacts(
            task_id="bee-task-alpha",
            template_id="template-alpha",
            topic_id="topic-alpha",
            status="completed",
            nodes=(
                BeeWorkspaceRunNode(
                    node_id="node-plan",
                    status="completed",
                    run_id="run-alpha",
                    action_ids=("action-alpha",),
                    validation_ids=("validation-alpha",),
                    attempts=1,
                ),
            ),
            run_ids=("run-alpha",),
            action_ids=("action-alpha",),
            validation_ids=("validation-alpha",),
            report_title="Local Bee task completed",
            report_summary="Validation passed with sanitized evidence.",
            evidence_labels=("pytest-smoke",),
            memory_candidates=(
                {"candidate_id": "memory-alpha", "status": "pending_review"},
            ),
        ),
    )

    task_json = json.loads(paths.task_json_path.read_text(encoding="utf-8"))
    assert task_json == {
        "artifact_role": "sanitized_mirror",
        "source_of_truth": "durable_bee_stores",
        "task_id": "bee-task-alpha",
        "template_id": "template-alpha",
        "topic_id": "topic-alpha",
        "status": "completed",
        "nodes": [
            {
                "node_id": "node-plan",
                "status": "completed",
                "run_id": "run-alpha",
                "action_ids": ["action-alpha"],
                "validation_ids": ["validation-alpha"],
                "attempts": 1,
            }
        ],
        "node_attempts": {"node-plan": 1},
        "run_ids": ["run-alpha"],
        "action_ids": ["action-alpha"],
        "validation_ids": ["validation-alpha"],
        "report_path": "report.md",
        "memory_candidates_path": "memory_candidates.yaml",
    }
    assert paths.report_path.read_text(encoding="utf-8") == (
        "# Local Bee task completed\n\n"
        "- task_id: bee-task-alpha\n"
        "- template_id: template-alpha\n"
        "- topic_id: topic-alpha\n"
        "- status: completed\n"
        "- summary: Validation passed with sanitized evidence.\n"
    )
    assert paths.evidence_dir.is_dir()
    assert paths.memory_candidates_path is not None
    assert "memory-alpha" in paths.memory_candidates_path.read_text(encoding="utf-8")


def test_bee_workspace_discovers_run_artifact_summaries(tmp_path: Path) -> None:
    write_bee_workspace_run_artifacts(
        tmp_path,
        BeeWorkspaceRunArtifacts(
            task_id="bee-task-alpha",
            template_id="template-alpha",
            topic_id="topic-alpha",
            status="completed",
            nodes=(
                BeeWorkspaceRunNode(
                    node_id="node-plan",
                    status="completed",
                    run_id="run-alpha",
                    action_ids=("action-alpha",),
                    validation_ids=("validation-alpha",),
                    attempts=1,
                ),
            ),
            run_ids=("run-alpha",),
            action_ids=("action-alpha",),
            validation_ids=("validation-alpha",),
            report_title="Local Bee task completed",
            report_summary="Validation passed with sanitized evidence.",
            memory_candidates=(
                {"candidate_id": "memory-alpha", "status": "pending_review"},
            ),
        ),
    )

    records = discover_bee_workspace_run_artifacts(tmp_path)

    assert len(records) == 1
    record = records[0]
    assert record.task_id == "bee-task-alpha"
    assert record.template_id == "template-alpha"
    assert record.topic_id == "topic-alpha"
    assert record.status == "completed"
    assert record.node_count == 1
    assert record.run_count == 1
    assert record.action_count == 1
    assert record.validation_count == 1
    assert record.has_report is True
    assert record.has_memory_candidates is True


@pytest.mark.parametrize(
    "raw_marker",
    [
        "command_output=raw output",
        "content=raw content",
        "message=raw message",
        "result=raw result",
        "stderr=raw stderr",
        "stdout=raw output",
        "text=raw text",
    ],
)
def test_bee_workspace_rejects_raw_report_fields(
    tmp_path: Path,
    raw_marker: str,
) -> None:
    with pytest.raises(ValueError, match="report_summary"):
        write_bee_workspace_run_artifacts(
            tmp_path,
            BeeWorkspaceRunArtifacts(
                task_id="bee-task-alpha",
                template_id="template-alpha",
                topic_id="topic-alpha",
                status="failed",
                nodes=(),
                report_title="Unsafe report",
                report_summary=raw_marker,
            ),
        )


def test_bee_workspace_rejects_raw_memory_candidate_fields(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="forbidden sensitive field"):
        write_bee_workspace_run_artifacts(
            tmp_path,
            BeeWorkspaceRunArtifacts(
                task_id="bee-task-alpha",
                template_id="template-alpha",
                topic_id="topic-alpha",
                status="completed",
                nodes=(),
                report_title="Safe report",
                report_summary="Safe summary",
                memory_candidates=({"stdout": "raw output"},),
            ),
        )


def test_bee_workspace_rejects_symlinked_run_artifact_file(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / ".bee" / "runs" / "bee-task-alpha"
    run_dir.mkdir(parents=True)
    outside_file = tmp_path / "outside-task.json"
    outside_file.write_text("{}", encoding="utf-8")
    (run_dir / "task.json").symlink_to(outside_file)

    with pytest.raises(ValueError, match="symlink"):
        write_bee_workspace_run_artifacts(
            tmp_path,
            BeeWorkspaceRunArtifacts(
                task_id="bee-task-alpha",
                template_id="template-alpha",
                topic_id="topic-alpha",
                status="completed",
                nodes=(),
                report_title="Safe report",
                report_summary="Safe summary",
            ),
        )


def test_bee_workspace_rejects_symlinked_bee_root_for_run_artifacts(
    tmp_path: Path,
) -> None:
    outside_dir = tmp_path / "outside-bee"
    outside_dir.mkdir()
    (tmp_path / ".bee").symlink_to(outside_dir, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        write_bee_workspace_run_artifacts(
            tmp_path,
            BeeWorkspaceRunArtifacts(
                task_id="bee-task-alpha",
                template_id="template-alpha",
                topic_id="topic-alpha",
                status="completed",
                nodes=(),
                report_title="Safe report",
                report_summary="Safe summary",
            ),
        )


@pytest.mark.parametrize(
    ("metadata_key", "expected"),
    [
        ("prompt", "forbidden sensitive field"),
        ("command", "forbidden executable field"),
    ],
)
def test_bee_workspace_rejects_sensitive_template_fields(
    tmp_path: Path,
    metadata_key: str,
    expected: str,
) -> None:
    template_dir = tmp_path / ".bee" / "templates" / f"unsafe-{metadata_key}"
    feature_dir = template_dir / "features"
    feature_dir.mkdir(parents=True)
    (template_dir / "metadata.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                f"template_id: unsafe-{metadata_key}",
                "kind: maintenance",
                "profile: local",
                "title: Unsafe template",
                "topic:",
                "  session_id: session-alpha",
                "nodes: []",
                "metadata:",
                f"  {metadata_key}: unsafe value",
            ]
        ),
        encoding="utf-8",
    )
    (template_dir / "SKILL.md").write_text("# Unsafe Skill\n", encoding="utf-8")
    (feature_dir / "acceptance.feature").write_text(
        "Feature: unsafe template\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match=expected):
        load_bee_workspace_template(tmp_path, f"unsafe-{metadata_key}")


def test_bee_workspace_rejects_invalid_template_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="template_id"):
        load_bee_workspace_template(tmp_path, "../escape")


def test_bee_workspace_rejects_symlinked_template_dir(tmp_path: Path) -> None:
    outside_dir = tmp_path / "outside-template"
    _write_safe_template(outside_dir, template_id="template-link")
    templates_dir = tmp_path / ".bee" / "templates"
    templates_dir.mkdir(parents=True)
    (templates_dir / "template-link").symlink_to(outside_dir, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        discover_bee_workspace_templates(tmp_path)


def test_bee_workspace_rejects_symlinked_metadata_file(tmp_path: Path) -> None:
    template_dir = tmp_path / ".bee" / "templates" / "template-alpha"
    feature_dir = template_dir / "features"
    feature_dir.mkdir(parents=True)
    (template_dir / "SKILL.md").write_text("# Template Skill\n", encoding="utf-8")
    (feature_dir / "acceptance.feature").write_text(
        "Feature: safe local template\n", encoding="utf-8"
    )
    outside_metadata = tmp_path / "outside-metadata.yaml"
    outside_metadata.write_text(_safe_template_yaml("template-alpha"), encoding="utf-8")
    (template_dir / "metadata.yaml").symlink_to(outside_metadata)

    with pytest.raises(ValueError, match="symlink"):
        load_bee_workspace_template(tmp_path, "template-alpha")


def _write_safe_template(template_dir: Path, *, template_id: str) -> None:
    feature_dir = template_dir / "features"
    feature_dir.mkdir(parents=True)
    (template_dir / "metadata.yaml").write_text(
        _safe_template_yaml(template_id),
        encoding="utf-8",
    )
    (template_dir / "SKILL.md").write_text("# Template Skill\n", encoding="utf-8")
    (feature_dir / "acceptance.feature").write_text(
        "Feature: safe local template\n", encoding="utf-8"
    )


def _safe_template_yaml(template_id: str) -> str:
    return "\n".join(
        [
            "version: 1",
            f"template_id: {template_id}",
            "kind: maintenance",
            "profile: local",
            "title: Local template",
            "topic:",
            "  session_id: session-alpha",
            "nodes:",
            "  - node_id: node-plan",
            "    kind: analysis",
            "    profile: default",
            "    title: Plan local task",
        ]
    )
