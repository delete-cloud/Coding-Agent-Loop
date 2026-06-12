from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.evaluation import (
    ContextSystemGoldenFailure,
    evaluate_context_system_golden_cases,
    load_context_system_golden_cases,
)


GOLDEN_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "eval"
    / "golden"
    / "context-system-retrieval-context-pack.yaml"
)


@pytest.mark.asyncio
async def test_context_system_golden_cases_cover_retrieval_and_context_pack(
    tmp_path: Path,
) -> None:
    results = await evaluate_context_system_golden_cases(
        GOLDEN_PATH,
        workspace_dir=tmp_path,
    )

    assert len(results) == 6
    result = next(
        result for result in results if result.case_id == "auth-retrieval-context-pack"
    )
    assert result.case_id == "auth-retrieval-context-pack"
    assert result.message_count == 1
    assert "## Repo references" in result.rendered_context
    assert "## Test failures" in result.rendered_context
    assert "- [Repo] src/auth.py" in result.rendered_context
    assert (
        "- [Test Failure] tests/test_auth.py::test_rejects_expired_token"
        in result.rendered_context
    )
    assert "billing invoice total" not in result.rendered_context
    threshold_result = next(
        result
        for result in results
        if result.case_id == "rag-distance-threshold-negative"
    )
    assert threshold_result.selected_scores
    assert max(threshold_result.selected_scores) <= 0.1
    assert "Authentication module with JWT token validation" not in (
        threshold_result.rendered_context
    )


def test_load_context_system_golden_cases_resolves_fixture_paths() -> None:
    cases = load_context_system_golden_cases(GOLDEN_PATH)

    assert len(cases) == 6
    case = cases[0]
    assert case.case_id == "auth-retrieval-context-pack"
    assert case.query == "expired auth token failure"
    assert case.top_k == 2
    assert case.corpus == "default"
    assert case.search_corpora == ("default",)
    assert case.expected.max_selected_score == 0.8
    assert [repo_file.path for repo_file in case.repo_files] == [
        Path("src/auth.py"),
        Path("src/billing.py"),
    ]
    assert case.test_failures == (
        ContextSystemGoldenFailure(
            command_label="uv run pytest tests/test_auth.py::test_rejects_expired_token",
            exit_code=1,
            test_node_id="tests/test_auth.py::test_rejects_expired_token",
            repo_path="tests/test_auth.py",
            line_start=18,
            line_end=18,
            fixture_path=(
                Path(__file__).resolve().parents[3]
                / "tests"
                / "fixtures"
                / "context_system"
                / "pytest_auth_failure.txt"
            ),
        ),
    )
    distance_case = next(
        case for case in cases if case.case_id == "rag-distance-threshold-negative"
    )
    assert distance_case.corpus == "notes"
    assert distance_case.search_corpora == ("notes",)
    assert distance_case.max_distance == 0.1
    assert distance_case.expected.max_selected_score == 0.1


def test_load_context_system_golden_cases_rejects_unsafe_case_id(
    tmp_path: Path,
) -> None:
    golden_path = tmp_path / "unsafe-id.yaml"
    _ = golden_path.write_text(
        """
version: 1
cases:
  - id: ../unsafe
    query: expired auth token failure
    repo_files:
      - path: src/auth.py
        content: auth fixture
    expected:
      rendered_contains: []
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="golden case id"):
        _ = load_context_system_golden_cases(golden_path)


def test_load_context_system_golden_cases_rejects_fixture_escape(
    tmp_path: Path,
) -> None:
    golden_path = tmp_path / "fixture-escape.yaml"
    _ = golden_path.write_text(
        """
version: 1
cases:
  - id: fixture-escape
    query: expired auth token failure
    test_failures:
      - command_label: uv run pytest tests/test_auth.py::test_rejects_expired_token
        exit_code: 1
        test_node_id: tests/test_auth.py::test_rejects_expired_token
        repo_path: tests/test_auth.py
        line_start: 18
        line_end: 18
        fixture: /tmp/outside-fixture.txt
    expected:
      rendered_contains: []
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must be under"):
        _ = load_context_system_golden_cases(golden_path)


@pytest.mark.asyncio
async def test_context_system_golden_cases_report_missing_rendered_snippet(
    tmp_path: Path,
) -> None:
    golden_path = tmp_path / "missing-snippet.yaml"
    _ = golden_path.write_text(
        f"""
version: 1
cases:
  - id: missing-snippet
    query: expired auth token failure
    repo_files:
      - path: src/auth.py
        content: |
          def validate_jwt(token):
              return "expired auth token accepted"
    test_failures:
      - command_label: uv run pytest tests/test_auth.py::test_rejects_expired_token
        exit_code: 1
        test_node_id: tests/test_auth.py::test_rejects_expired_token
        repo_path: tests/test_auth.py
        line_start: 18
        line_end: 18
        fixture: {GOLDEN_PATH.parents[3] / "tests" / "fixtures" / "context_system" / "pytest_auth_failure.txt"}
    expected:
      rendered_contains:
        - definitely missing text
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="missing-snippet"):
        _ = await evaluate_context_system_golden_cases(
            golden_path,
            workspace_dir=tmp_path / "workspace",
        )


@pytest.mark.asyncio
async def test_context_system_golden_cases_can_reuse_workspace(
    tmp_path: Path,
) -> None:
    first = await evaluate_context_system_golden_cases(
        GOLDEN_PATH,
        workspace_dir=tmp_path,
    )
    second = await evaluate_context_system_golden_cases(
        GOLDEN_PATH,
        workspace_dir=tmp_path,
    )

    assert first[0].message_count == 1
    assert second[0].message_count == 1
    auth_result = next(
        result for result in second if result.case_id == "auth-retrieval-context-pack"
    )
    assert "## Repo references" in auth_result.rendered_context
    assert "## Test failures" in auth_result.rendered_context
