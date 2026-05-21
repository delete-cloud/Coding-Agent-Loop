from __future__ import annotations

import pytest

from coding_agent.bee_runtime import BeeTaskManifest, parse_bee_task_manifest
from coding_agent.topic_store import JSONObject


def test_bee_manifest_parses_safe_fixture() -> None:
    manifest = parse_bee_task_manifest(_safe_manifest())

    assert manifest == BeeTaskManifest(
        version=1,
        kind="maintenance",
        profile="local",
        title="Refresh release docs",
        summary="Check release docs and validation status.",
        context_profile="repo",
        validation_profile="pytest",
        workspace_policy="default",
        topic=manifest.topic,
        nodes=manifest.nodes,
        metadata={"source": "fixture", "risk": "low"},
    )
    assert manifest.topic.session_id == "session-alpha"
    assert manifest.topic.topic_id == "topic-alpha"
    assert manifest.topic.tape_id == "tape-alpha"
    assert manifest.topic.metadata == {"source": "manual"}
    assert [node.node_id for node in manifest.nodes] == [
        "node-plan",
        "node-validate",
    ]
    assert manifest.nodes[1].depends_on == ("node-plan",)
    assert manifest.nodes[1].validation_profile == "pytest"
    assert manifest.nodes[1].metadata == {"expected_policy": "validation"}


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("metadata", "prompt"), "raw prompt"),
        (("metadata", "nested", "message"), "raw message"),
        (("nodes", 0, "metadata", "stdout"), "raw output"),
        (("topic", "metadata", "secret_name"), "token"),
        (("metadata", "environment", "GITHUB_TOKEN"), "ghp_example"),
        (("metadata", "api_key"), "abc123"),
        (("metadata", "password"), "abc123"),
        (("metadata", "bearer_token"), "abc123"),
        (("metadata", "GITHUB_TOKEN"), "abc123"),
        (("metadata", "AWS_SESSION_TOKEN"), "abc123"),
    ],
)
def test_bee_manifest_rejects_raw_sensitive_fields(
    path: tuple[str | int, ...],
    value: str,
) -> None:
    raw = _safe_manifest()
    _set_path(raw, path, value)

    with pytest.raises(ValueError, match="forbidden sensitive field"):
        parse_bee_task_manifest(raw)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("metadata", "safe_marker"), "token=abc123"),
        (("nodes", 0, "metadata", "safe_label"), "sk-test-value"),
        (("topic", "title_hint"), "secret=hidden"),
        (("metadata", "safe_marker_2"), "ghp_example"),
    ],
)
def test_bee_manifest_rejects_secret_like_values(
    path: tuple[str | int, ...],
    value: str,
) -> None:
    raw = _safe_manifest()
    _set_path(raw, path, value)

    with pytest.raises(ValueError, match="secret-like value"):
        parse_bee_task_manifest(raw)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("nodes", 0, "command"), "pytest"),
        (("nodes", 0, "run_command"), "pytest"),
        (("nodes", 0, "runCommand"), "pytest"),
        (("nodes", 0, "shell_command"), "pytest"),
        (("nodes", 0, "shellCommand"), "pytest"),
        (("nodes", 0, "command_spec"), "pytest"),
        (("nodes", 0, "commandSpec"), "pytest"),
        (("nodes", 0, "pre_commands"), "pytest"),
        (("nodes", 0, "preCommands"), "pytest"),
        (("nodes", 0, "metadata", "executor"), "local"),
        (("metadata", "script"), "run checks"),
    ],
)
def test_bee_manifest_rejects_executable_fields(
    path: tuple[str | int, ...],
    value: str,
) -> None:
    raw = _safe_manifest()
    _set_path(raw, path, value)

    with pytest.raises(ValueError, match="forbidden executable field"):
        parse_bee_task_manifest(raw)


def test_bee_manifest_rejects_unknown_node_dependencies() -> None:
    raw = _safe_manifest()
    raw["nodes"][1]["depends_on"] = ["node-missing"]  # type: ignore[index]

    with pytest.raises(ValueError, match="dependencies not found: node-missing"):
        parse_bee_task_manifest(raw)


def test_bee_manifest_rejects_self_dependency() -> None:
    raw = _safe_manifest()
    raw["nodes"][0]["depends_on"] = ["node-plan"]  # type: ignore[index]

    with pytest.raises(ValueError, match="cannot depend on itself: node-plan"):
        parse_bee_task_manifest(raw)


def test_bee_manifest_rejects_dependency_cycles() -> None:
    raw = _safe_manifest()
    raw["nodes"][0]["depends_on"] = ["node-validate"]  # type: ignore[index]
    raw["nodes"][1]["depends_on"] = ["node-plan"]  # type: ignore[index]

    with pytest.raises(ValueError, match="dependency cycle includes"):
        parse_bee_task_manifest(raw)


def _safe_manifest() -> JSONObject:
    return {
        "version": 1,
        "kind": "maintenance",
        "profile": "local",
        "title": "Refresh release docs",
        "summary": "Check release docs and validation status.",
        "context_profile": "repo",
        "validation_profile": "pytest",
        "workspace_policy": "default",
        "topic": {
            "session_id": "session-alpha",
            "topic_id": "topic-alpha",
            "tape_id": "tape-alpha",
            "title_hint": "Release docs",
            "metadata": {"source": "manual"},
        },
        "nodes": [
            {
                "node_id": "node-plan",
                "kind": "analysis",
                "profile": "default",
                "title": "Plan update",
                "depends_on": [],
                "context_profile": "repo",
                "metadata": {"expected_policy": "read_only"},
            },
            {
                "node_id": "node-validate",
                "kind": "validation",
                "profile": "default",
                "title": "Run validation",
                "depends_on": ["node-plan"],
                "validation_profile": "pytest",
                "metadata": {"expected_policy": "validation"},
            },
        ],
        "metadata": {"source": "fixture", "risk": "low"},
    }


def _set_path(raw: JSONObject, path: tuple[str | int, ...], value: str) -> None:
    cursor: object = raw
    for index, part in enumerate(path[:-1]):
        if isinstance(part, int):
            cursor = cursor[part]  # type: ignore[index]
        else:
            next_part = path[index + 1]
            if (
                isinstance(cursor, dict)
                and part not in cursor
                and isinstance(next_part, str)
            ):
                cursor[part] = {}
            cursor = cursor[part]  # type: ignore[index]
    last = path[-1]
    if isinstance(last, int):
        cursor[last] = value  # type: ignore[index]
    else:
        cursor[last] = value  # type: ignore[index]
