from __future__ import annotations

from pathlib import Path

import pytest

from agentkit.tape.extract import Visibility

from coding_agent.evaluation import (
    EvaluationManifest,
    build_manifest_test_cases,
    load_evaluation_manifest,
)


FIXTURE_DIR = Path(__file__).resolve().parents[3] / "data" / "eval"
MANIFEST_PATH = FIXTURE_DIR / "golden" / "context-system-manifest.yaml"


def test_load_evaluation_manifest_resolves_local_fixture_paths() -> None:
    manifest = load_evaluation_manifest(MANIFEST_PATH)

    assert manifest == EvaluationManifest(
        version=1,
        cases=manifest.cases,
    )
    assert len(manifest.cases) == 1
    case = manifest.cases[0]
    assert case.case_id == "parent-child-subagent-baseline"
    assert case.tape_path == FIXTURE_DIR / "golden" / "parent-child-subagent-001.jsonl"
    assert case.spec_path == FIXTURE_DIR / "golden" / "parent-child-subagent-001.yaml"
    assert case.visibility is Visibility.VISIBLE
    assert case.tags == ("context-system", "baseline")
    assert case.metadata == {"area": "evaluation-harness"}


def test_evaluation_manifest_builds_context_system_cases_from_local_fixtures() -> None:
    cases = build_manifest_test_cases(MANIFEST_PATH)

    assert len(cases) == 1
    case = cases[0]
    assert case.input == "parent task"
    assert case.actual_output == "parent done"
    assert [tool.name for tool in case.tools_called] == ["subagent"]
    assert [tool.name for tool in case.expected_tools] == ["subagent"]
    assert case.metadata == {
        "task": "Run a child task and report back to the parent",
        "forbidden_tools": ["bash_run"],
        "threshold": 1.0,
        "manifest_case_id": "parent-child-subagent-baseline",
        "manifest_tags": ["context-system", "baseline"],
        "manifest_metadata": {"area": "evaluation-harness"},
    }


def test_evaluation_manifest_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    tape_path = FIXTURE_DIR / "golden" / "parent-child-subagent-001.jsonl"
    spec_path = FIXTURE_DIR / "golden" / "parent-child-subagent-001.yaml"
    manifest_path = tmp_path / "manifest.yaml"
    _ = manifest_path.write_text(
        f"""
version: 1
cases:
  - id: duplicate
    tape: {tape_path}
    spec: {spec_path}
  - id: duplicate
    tape: {tape_path}
    spec: {spec_path}
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate"):
        _ = load_evaluation_manifest(manifest_path)


def test_evaluation_manifest_rejects_non_string_metadata_keys(
    tmp_path: Path,
) -> None:
    tape_path = FIXTURE_DIR / "golden" / "parent-child-subagent-001.jsonl"
    spec_path = FIXTURE_DIR / "golden" / "parent-child-subagent-001.yaml"
    manifest_path = tmp_path / "manifest.yaml"
    _ = manifest_path.write_text(
        f"""
version: 1
cases:
  - id: invalid-metadata
    tape: {tape_path}
    spec: {spec_path}
    metadata:
      123: value
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(TypeError, match="keys must be strings"):
        _ = load_evaluation_manifest(manifest_path)
