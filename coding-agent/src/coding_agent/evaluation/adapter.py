from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import yaml

from agentkit._types import JsonDict
from agentkit.tape.extract import TurnTrace, Visibility, extract_turns
from agentkit.tape.models import Entry


@dataclass(frozen=True)
class EvaluationToolCall:
    name: str
    input_parameters: JsonDict | None = None
    output: object | None = None
    description: str | None = None
    reasoning: str | None = None


@dataclass(frozen=True)
class GoldenTurnSpec:
    task: str
    expected_tools: tuple[EvaluationToolCall, ...]
    forbidden_tools: tuple[str, ...] = ()
    threshold: float | None = None


@dataclass(frozen=True)
class EvaluationTestCase:
    input: str
    actual_output: str
    tools_called: tuple[EvaluationToolCall, ...]
    expected_tools: tuple[EvaluationToolCall, ...]
    metadata: dict[str, object] = field(default_factory=dict)


def _json_mapping(value: object, *, context: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{context} must be a mapping")
    return cast(Mapping[str, object], value)


def _optional_json_dict(value: object, *, context: str) -> JsonDict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError(f"{context} must be a mapping when provided")
    return cast(JsonDict, value)


def _load_json_line(line: str) -> Mapping[str, object]:
    return cast(Mapping[str, object], json.loads(line))


def _load_yaml_mapping(text: str) -> Mapping[str, object]:
    return cast(Mapping[str, object], yaml.safe_load(text))


def _string_list(value: object, *, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{context} must be a list of strings")
    items = cast(list[object], value)
    strings: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise TypeError(f"{context} must be a list of strings")
        strings.append(item)
    return tuple(strings)


def _object_list(value: object, *, context: str) -> tuple[object, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{context} must be a list")
    return tuple(cast(list[object], value))


def load_tape_entries(path: Path) -> tuple[Entry, ...]:
    entries: list[Entry] = []
    with path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            entries.append(
                Entry.from_dict(
                    _json_mapping(_load_json_line(line), context="tape entry")
                )
            )
    return tuple(entries)


def _tool_call_from_dict(data: Mapping[str, object]) -> EvaluationToolCall:
    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("expected tool entry must include a non-empty name")

    input_parameters = _optional_json_dict(
        data.get("input_parameters"),
        context="input_parameters",
    )

    description = data.get("description")
    if description is not None and not isinstance(description, str):
        raise TypeError("description must be a string when provided")

    reasoning = data.get("reasoning")
    if reasoning is not None and not isinstance(reasoning, str):
        raise TypeError("reasoning must be a string when provided")

    return EvaluationToolCall(
        name=name,
        input_parameters=dict(input_parameters)
        if input_parameters is not None
        else None,
        output=data.get("output"),
        description=description,
        reasoning=reasoning,
    )


def load_golden_spec(path: Path) -> GoldenTurnSpec:
    payload = _json_mapping(
        _load_yaml_mapping(path.read_text(encoding="utf-8")), context="golden spec"
    )

    task = payload.get("task")
    if not isinstance(task, str) or not task:
        raise ValueError("golden spec must include a non-empty task")

    expected_tools_raw = _object_list(
        payload.get("expected_tools", []),
        context="expected_tools",
    )

    forbidden_tools_raw = _string_list(
        payload.get("forbidden_tools", []),
        context="forbidden_tools",
    )

    threshold = payload.get("threshold")
    if threshold is not None and not isinstance(threshold, int | float):
        raise TypeError("threshold must be numeric when provided")

    return GoldenTurnSpec(
        task=task,
        expected_tools=tuple(
            _tool_call_from_dict(_json_mapping(item, context="expected tool entry"))
            for item in expected_tools_raw
        ),
        forbidden_tools=forbidden_tools_raw,
        threshold=float(threshold) if isinstance(threshold, int | float) else None,
    )


def _turn_tool_calls(turn: TurnTrace) -> tuple[EvaluationToolCall, ...]:
    return tuple(
        EvaluationToolCall(
            name=tool.name,
            input_parameters=dict(tool.arguments),
            output=tool.result_content,
        )
        for tool in turn.tool_calls
    )


def turn_to_test_case(
    turn: TurnTrace,
    *,
    spec: GoldenTurnSpec,
) -> EvaluationTestCase:
    metadata: dict[str, object] = {"task": spec.task}
    if spec.forbidden_tools:
        metadata["forbidden_tools"] = list(spec.forbidden_tools)
    if spec.threshold is not None:
        metadata["threshold"] = spec.threshold

    return EvaluationTestCase(
        input=turn.user_input,
        actual_output=turn.final_output or "",
        tools_called=_turn_tool_calls(turn),
        expected_tools=spec.expected_tools,
        metadata=metadata,
    )


def build_test_cases(
    *,
    tape_path: Path,
    spec_path: Path,
    visibility: Visibility = Visibility.VISIBLE,
) -> list[EvaluationTestCase]:
    entries = load_tape_entries(tape_path)
    turns = extract_turns(entries, visibility=visibility)
    if len(turns) != 1:
        raise ValueError("build_test_cases currently supports single-turn tapes only")
    spec = load_golden_spec(spec_path)
    return [turn_to_test_case(turn, spec=spec) for turn in turns]
