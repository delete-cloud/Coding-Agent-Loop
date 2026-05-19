from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import yaml

from agentkit.tape.extract import Visibility

from coding_agent.evaluation.adapter import EvaluationTestCase, build_test_cases


@dataclass(frozen=True)
class EvaluationManifestCase:
    case_id: str
    tape_path: Path
    spec_path: Path
    visibility: Visibility = Visibility.VISIBLE
    tags: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluationManifest:
    version: int
    cases: tuple[EvaluationManifestCase, ...]


def load_evaluation_manifest(path: Path) -> EvaluationManifest:
    payload = _mapping(
        yaml.safe_load(path.read_text(encoding="utf-8")),
        context="evaluation manifest",
    )

    version = _version(payload.get("version"))
    cases = _case_list(payload.get("cases", []))
    manifest_cases: list[EvaluationManifestCase] = []
    seen_case_ids: set[str] = set()
    for index, raw_case in enumerate(cases):
        case = _manifest_case_from_mapping(
            _mapping(raw_case, context=f"manifest case {index}"),
            manifest_path=path,
        )
        if case.case_id in seen_case_ids:
            raise ValueError(f"duplicate manifest case id: {case.case_id}")
        seen_case_ids.add(case.case_id)
        manifest_cases.append(case)

    if not manifest_cases:
        raise ValueError("evaluation manifest must include at least one case")

    return EvaluationManifest(version=version, cases=tuple(manifest_cases))


def build_manifest_test_cases(manifest_path: Path) -> list[EvaluationTestCase]:
    manifest = load_evaluation_manifest(manifest_path)
    cases: list[EvaluationTestCase] = []
    for manifest_case in manifest.cases:
        built_cases = build_test_cases(
            tape_path=manifest_case.tape_path,
            spec_path=manifest_case.spec_path,
            visibility=manifest_case.visibility,
        )
        cases.extend(
            _with_manifest_metadata(case, manifest_case=manifest_case)
            for case in built_cases
        )
    return cases


def _manifest_case_from_mapping(
    data: Mapping[str, object],
    *,
    manifest_path: Path,
) -> EvaluationManifestCase:
    case_id = _non_empty_str(data.get("id"), context="manifest case id")
    tape_path = _fixture_path(
        data.get("tape"),
        manifest_path=manifest_path,
        context=f"manifest case {case_id} tape",
    )
    spec_path = _fixture_path(
        data.get("spec"),
        manifest_path=manifest_path,
        context=f"manifest case {case_id} spec",
    )
    visibility = _visibility(data.get("visibility", Visibility.VISIBLE.value))
    tags = _string_tuple(data.get("tags", []), context=f"manifest case {case_id} tags")
    metadata = _metadata_mapping(
        data.get("metadata", {}),
        context=f"manifest case {case_id} metadata",
    )
    return EvaluationManifestCase(
        case_id=case_id,
        tape_path=tape_path,
        spec_path=spec_path,
        visibility=visibility,
        tags=tags,
        metadata=dict(metadata),
    )


def _with_manifest_metadata(
    case: EvaluationTestCase,
    *,
    manifest_case: EvaluationManifestCase,
) -> EvaluationTestCase:
    metadata = dict(case.metadata)
    metadata["manifest_case_id"] = manifest_case.case_id
    metadata["manifest_tags"] = list(manifest_case.tags)
    metadata["manifest_metadata"] = dict(manifest_case.metadata)
    return EvaluationTestCase(
        input=case.input,
        actual_output=case.actual_output,
        tools_called=case.tools_called,
        expected_tools=case.expected_tools,
        metadata=metadata,
    )


def _mapping(value: object, *, context: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{context} must be a mapping")
    return cast(Mapping[str, object], value)


def _metadata_mapping(value: object, *, context: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{context} must be a mapping")
    for key in value:
        if not isinstance(key, str):
            raise TypeError(f"{context} keys must be strings")
    return cast(Mapping[str, object], value)


def _case_list(value: object) -> tuple[object, ...]:
    if not isinstance(value, list):
        raise TypeError("evaluation manifest cases must be a list")
    return tuple(cast(list[object], value))


def _string_tuple(value: object, *, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{context} must be a list of strings")
    tags: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, str) or not item:
            raise TypeError(f"{context} must be a list of non-empty strings")
        tags.append(item)
    return tuple(tags)


def _non_empty_str(value: object, *, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} must be a non-empty string")
    return value


def _version(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("evaluation manifest version must be an integer")
    if value != 1:
        raise ValueError("evaluation manifest version must be 1")
    return value


def _fixture_path(value: object, *, manifest_path: Path, context: str) -> Path:
    raw_path = Path(_non_empty_str(value, context=context))
    if raw_path.is_absolute():
        return raw_path.resolve()
    return (manifest_path.parent / raw_path).resolve()


def _visibility(value: object) -> Visibility:
    raw_visibility = _non_empty_str(value, context="manifest case visibility")
    try:
        return Visibility(raw_visibility)
    except ValueError as exc:
        raise ValueError(
            f"manifest case visibility must be one of: "
            f"{', '.join(item.value for item in Visibility)}"
        ) from exc
