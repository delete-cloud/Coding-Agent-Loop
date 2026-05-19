from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.verification import load_release_verification_manifest


EXPECTED_RELEASE_COMMANDS = [
    "uv run pytest tests/integration/test_durable_runtime_smoke.py -v",
    "uv run pytest tests/coding_agent/test_context_system_smoke.py -v",
    "uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v",
    "uv run pytest tests/coding_agent/evaluation/ -v",
    (
        "uv run pytest tests/agentkit/runtime/test_pipeline.py "
        '-k "build_context or runtime_stage_spans" -v'
    ),
]


def test_release_verification_manifest_lists_preserved_baseline_commands() -> None:
    manifest = load_release_verification_manifest(
        Path("docs/release_hardening/release-verification.yaml")
    )

    assert manifest.name == "release-hardening-g38-g45"
    assert [gate.id for gate in manifest.gates] == [
        "durable-runtime-smoke",
        "context-system-smoke",
        "action-safety-smoke",
        "evaluation-suite",
        "agentkit-context-pipeline",
    ]
    assert [gate.command for gate in manifest.gates] == EXPECTED_RELEASE_COMMANDS
    assert all(gate.required is True for gate in manifest.gates)
    assert manifest.to_verification_contract().steps[0].command == (
        "uv run pytest tests/integration/test_durable_runtime_smoke.py -v"
    )


def test_release_verification_manifest_rejects_duplicate_gate_ids(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "release.yaml"
    _ = manifest_path.write_text(
        """
name: duplicate
description: duplicate gate ids
gates:
  - id: repeated
    command: python -c pass
    scope: test
  - id: repeated
    command: python -c pass
    scope: test
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate gate id"):
        _ = load_release_verification_manifest(manifest_path)


def test_release_verification_manifest_rejects_shell_syntax(tmp_path: Path) -> None:
    manifest_path = tmp_path / "release.yaml"
    _ = manifest_path.write_text(
        """
name: unsafe
description: shell syntax rejection
gates:
  - id: shell
    command: pytest tests && echo done
    scope: test
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="shell syntax"):
        _ = load_release_verification_manifest(manifest_path)
