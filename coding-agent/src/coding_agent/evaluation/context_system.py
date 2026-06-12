from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from typing import cast

import yaml

from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape

from coding_agent.kb import KB
from coding_agent.plugins.kb import KBPlugin

_CASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class ContextSystemGoldenRepoFile:
    path: Path
    content: str


@dataclass(frozen=True)
class ContextSystemGoldenFailure:
    command_label: str
    exit_code: int
    test_node_id: str
    repo_path: str
    line_start: int
    line_end: int
    fixture_path: Path


@dataclass(frozen=True)
class ContextSystemGoldenExpectation:
    rendered_contains: tuple[str, ...] = ()
    rendered_excludes: tuple[str, ...] = ()
    min_selected_score: float | None = None
    max_selected_score: float | None = None


@dataclass(frozen=True)
class ContextSystemGoldenCase:
    case_id: str
    query: str
    repo_files: tuple[ContextSystemGoldenRepoFile, ...]
    test_failures: tuple[ContextSystemGoldenFailure, ...]
    expected: ContextSystemGoldenExpectation
    top_k: int = 5
    corpus: str = "default"
    search_corpora: tuple[str, ...] | None = None
    max_distance: float | None = None


@dataclass(frozen=True)
class ContextSystemGoldenResult:
    case_id: str
    rendered_context: str
    message_count: int
    selected_scores: tuple[float, ...]


def load_context_system_golden_cases(path: Path) -> tuple[ContextSystemGoldenCase, ...]:
    payload = _mapping(
        yaml.safe_load(path.read_text(encoding="utf-8")),
        context="context-system golden file",
    )
    version = payload.get("version")
    if version != 1:
        raise ValueError("context-system golden file version must be 1")

    raw_cases = _object_list(payload.get("cases", []), context="golden cases")
    cases = tuple(
        _golden_case_from_mapping(
            _mapping(raw_case, context=f"golden case {index}"),
            golden_path=path,
        )
        for index, raw_case in enumerate(raw_cases)
    )
    if not cases:
        raise ValueError("context-system golden file must include at least one case")
    _raise_for_duplicate_case_ids(cases)
    return cases


async def evaluate_context_system_golden_cases(
    path: Path,
    *,
    workspace_dir: Path,
) -> list[ContextSystemGoldenResult]:
    cases = load_context_system_golden_cases(path)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    results: list[ContextSystemGoldenResult] = []
    for case in cases:
        with TemporaryDirectory(
            prefix="context-system-golden-",
            dir=workspace_dir,
        ) as raw_case_dir:
            result = await _evaluate_golden_case(
                case,
                case_dir=Path(raw_case_dir),
            )
        _assert_expectations(case, result)
        results.append(result)
    return results


def _golden_case_from_mapping(
    data: Mapping[str, object],
    *,
    golden_path: Path,
) -> ContextSystemGoldenCase:
    case_id = _case_id(data.get("id"))
    query = _non_empty_str(data.get("query"), context=f"golden case {case_id} query")
    top_k = _positive_int(data.get("top_k", 5), context=f"golden case {case_id} top_k")
    corpus = _non_empty_str(
        data.get("corpus", "default"),
        context=f"golden case {case_id} corpus",
    )
    search_corpora = _optional_string_tuple(
        data.get("search_corpora"),
        context=f"golden case {case_id} search_corpora",
    )
    max_distance = _optional_non_negative_float(
        data.get("max_distance"),
        context=f"golden case {case_id} max_distance",
    )
    repo_files = tuple(
        _repo_file_from_mapping(
            _mapping(repo_file, context=f"golden case {case_id} repo file")
        )
        for repo_file in _object_list(
            data.get("repo_files", []),
            context=f"golden case {case_id} repo_files",
        )
    )
    test_failures = tuple(
        _failure_from_mapping(
            _mapping(failure, context=f"golden case {case_id} test failure"),
            golden_path=golden_path,
        )
        for failure in _object_list(
            data.get("test_failures", []),
            context=f"golden case {case_id} test_failures",
        )
    )
    if not repo_files and not test_failures:
        raise ValueError(f"golden case {case_id} must include at least one source")
    expected = _expectation_from_mapping(
        _mapping(data.get("expected", {}), context=f"golden case {case_id} expected")
    )
    return ContextSystemGoldenCase(
        case_id=case_id,
        query=query,
        repo_files=repo_files,
        test_failures=test_failures,
        expected=expected,
        top_k=top_k,
        corpus=corpus,
        search_corpora=search_corpora,
        max_distance=max_distance,
    )


def _repo_file_from_mapping(data: Mapping[str, object]) -> ContextSystemGoldenRepoFile:
    raw_path = Path(_non_empty_str(data.get("path"), context="repo file path"))
    _require_relative_path(raw_path, context="repo file path")
    content = _non_empty_str(data.get("content"), context="repo file content")
    return ContextSystemGoldenRepoFile(path=raw_path, content=content)


def _failure_from_mapping(
    data: Mapping[str, object],
    *,
    golden_path: Path,
) -> ContextSystemGoldenFailure:
    fixture_path = _fixture_path(
        data.get("fixture"),
        golden_path=golden_path,
        context="test failure fixture",
    )
    return ContextSystemGoldenFailure(
        command_label=_non_empty_str(
            data.get("command_label"),
            context="test failure command_label",
        ),
        exit_code=_int(data.get("exit_code"), context="test failure exit_code"),
        test_node_id=_non_empty_str(
            data.get("test_node_id"),
            context="test failure test_node_id",
        ),
        repo_path=_non_empty_str(
            data.get("repo_path"), context="test failure repo_path"
        ),
        line_start=_positive_int(
            data.get("line_start"),
            context="test failure line_start",
        ),
        line_end=_positive_int(data.get("line_end"), context="test failure line_end"),
        fixture_path=fixture_path,
    )


def _expectation_from_mapping(
    data: Mapping[str, object],
) -> ContextSystemGoldenExpectation:
    return ContextSystemGoldenExpectation(
        rendered_contains=_string_tuple(
            data.get("rendered_contains", []),
            context="rendered_contains",
        ),
        rendered_excludes=_string_tuple(
            data.get("rendered_excludes", []),
            context="rendered_excludes",
        ),
        min_selected_score=_optional_non_negative_float(
            data.get("min_selected_score"),
            context="min_selected_score",
        ),
        max_selected_score=_optional_non_negative_float(
            data.get("max_selected_score"),
            context="max_selected_score",
        ),
    )


async def _evaluate_golden_case(
    case: ContextSystemGoldenCase,
    *,
    case_dir: Path,
) -> ContextSystemGoldenResult:
    repo_root = case_dir / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    for repo_file in case.repo_files:
        target_path = repo_root / repo_file.path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(repo_file.content, encoding="utf-8")

    db_path = case_dir / "kb_db"
    kb = KB(
        db_path=db_path,
        embedding_dim=4,
        embedding_fn=_context_system_embed,
        chunk_size=1000,
        chunk_overlap=0,
        corpus=case.corpus,
    )
    if case.repo_files:
        await kb.index_directory(repo_root, show_progress=False)
    for failure in case.test_failures:
        await kb.index_test_failure(
            command_label=failure.command_label,
            exit_code=failure.exit_code,
            test_node_id=failure.test_node_id,
            repo_path=failure.repo_path,
            line_start=failure.line_start,
            line_end=failure.line_end,
            failure_snippet=failure.fixture_path.read_text(encoding="utf-8"),
        )

    plugin = KBPlugin(
        db_path=db_path,
        embedding_dim=4,
        top_k=case.top_k,
        max_distance=case.max_distance,
        embedding_fn=_context_system_embed,
        search_corpora=case.search_corpora,
    )
    plugin.do_mount()
    tape = Tape()
    tape.append(
        Entry(
            kind="message",
            payload={"role": "user", "content": case.query},
        )
    )
    messages = plugin.build_context(tape=tape)
    rendered_context = "\n".join(
        content
        for message in messages
        if isinstance((content := message.get("content")), str)
    )
    return ContextSystemGoldenResult(
        case_id=case.case_id,
        rendered_context=rendered_context,
        message_count=len(messages),
        selected_scores=(
            tuple(result.score for result in plugin._snapshot.retrieval_results)
            if plugin._snapshot is not None
            else ()
        ),
    )


def _assert_expectations(
    case: ContextSystemGoldenCase,
    result: ContextSystemGoldenResult,
) -> None:
    for expected in case.expected.rendered_contains:
        if expected not in result.rendered_context:
            raise AssertionError(
                f"{case.case_id}: rendered context missing expected text: {expected}"
            )
    for unexpected in case.expected.rendered_excludes:
        if unexpected in result.rendered_context:
            raise AssertionError(
                f"{case.case_id}: rendered context included excluded text: {unexpected}"
            )
    if case.expected.min_selected_score is not None and (
        not result.selected_scores
        or min(result.selected_scores) < case.expected.min_selected_score
    ):
        raise AssertionError(
            f"{case.case_id}: selected score below expected minimum "
            f"{case.expected.min_selected_score}: {result.selected_scores}"
        )
    if case.expected.max_selected_score is not None and (
        not result.selected_scores
        or max(result.selected_scores) > case.expected.max_selected_score
    ):
        raise AssertionError(
            f"{case.case_id}: selected score above expected maximum "
            f"{case.expected.max_selected_score}: {result.selected_scores}"
        )


def _context_system_embed(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for text in texts:
        lower = text.lower()
        if "auth" in lower or "jwt" in lower or "expired token" in lower:
            vectors.append([1.0, 0.0, 0.0, 0.0])
        elif "billing" in lower or "invoice" in lower:
            vectors.append([0.0, 1.0, 0.0, 0.0])
        elif (
            "longhorn" in lower
            or "restore" in lower
            or "netbird" in lower
            or "cilium" in lower
            or "mtu" in lower
        ):
            vectors.append([0.0, 0.0, 1.0, 0.0])
        elif (
            "adr" in lower
            or "fencing" in lower
            or "sqlite" in lower
            or "corpus" in lower
            or "retrieval" in lower
        ):
            vectors.append([0.0, 0.0, 0.0, 1.0])
        else:
            vectors.append([0.5, 0.5, 0.5, 0.5])
    return vectors


def _mapping(value: object, *, context: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{context} must be a mapping")
    return cast(Mapping[str, object], value)


def _object_list(value: object, *, context: str) -> tuple[object, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{context} must be a list")
    return tuple(cast(list[object], value))


def _string_tuple(value: object, *, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{context} must be a list of strings")
    strings: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, str) or not item:
            raise TypeError(f"{context} must be a list of non-empty strings")
        strings.append(item)
    return tuple(strings)


def _optional_string_tuple(value: object, *, context: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    return _string_tuple(value, context=context)


def _non_empty_str(value: object, *, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} must be a non-empty string")
    return value


def _int(value: object, *, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{context} must be an integer")
    return value


def _optional_non_negative_float(value: object, *, context: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise TypeError(f"{context} must be a number")
    number = float(value)
    if number < 0:
        raise ValueError(f"{context} must be non-negative")
    return number


def _positive_int(value: object, *, context: str) -> int:
    value_int = _int(value, context=context)
    if value_int <= 0:
        raise ValueError(f"{context} must be positive")
    return value_int


def _fixture_path(value: object, *, golden_path: Path, context: str) -> Path:
    raw_path = Path(_non_empty_str(value, context=context))
    if not raw_path.is_absolute():
        raw_path = golden_path.parent / raw_path
    resolved_path = raw_path.resolve()
    fixture_root = _fixture_root()
    if not resolved_path.is_relative_to(fixture_root):
        raise ValueError(f"{context} must be under {fixture_root}")
    return resolved_path


def _require_relative_path(path: Path, *, context: str) -> None:
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{context} must be repo-relative")


def _case_id(value: object) -> str:
    case_id = _non_empty_str(value, context="golden case id")
    if _CASE_ID_PATTERN.fullmatch(case_id) is None:
        raise ValueError(
            "golden case id must contain only letters, numbers, dots, underscores, "
            "and hyphens"
        )
    return case_id


def _fixture_root() -> Path:
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures"


def _raise_for_duplicate_case_ids(cases: tuple[ContextSystemGoldenCase, ...]) -> None:
    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            raise ValueError(f"duplicate context-system golden case id: {case.case_id}")
        seen.add(case.case_id)
