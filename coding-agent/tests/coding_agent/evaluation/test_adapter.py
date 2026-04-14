from __future__ import annotations

from pathlib import Path

import pytest

from agentkit.tape.extract import ToolCallRecord, TurnTrace

from coding_agent.evaluation import (
    EvaluationToolCall,
    GoldenTurnSpec,
    build_test_cases,
    load_golden_spec,
    load_tape_entries,
    turn_to_test_case,
)


FIXTURE_DIR = Path(__file__).resolve().parents[3] / "data" / "eval" / "golden"


class TestTurnToTestCase:
    def test_turn_to_test_case_keeps_observed_and_expected_tools_separate(self) -> None:
        turn = TurnTrace(
            user_input="Rename old_name to new_name",
            tool_calls=(
                ToolCallRecord(
                    call_id="call-1",
                    name="file_read",
                    arguments={"path": "src/app.py"},
                    result_content="def old_name():\n    return 42\n",
                ),
                ToolCallRecord(
                    call_id="call-2",
                    name="file_replace",
                    arguments={
                        "path": "src/app.py",
                        "old": "def old_name():",
                        "new": "def new_name():",
                    },
                    result_content="Replaced 1 occurrence in src/app.py",
                ),
            ),
            final_output="Done.",
        )
        spec = GoldenTurnSpec(
            task="Rename old_name to new_name",
            expected_tools=(
                EvaluationToolCall(
                    name="file_read",
                    input_parameters={"path": "src/app.py"},
                ),
                EvaluationToolCall(
                    name="file_replace",
                    input_parameters={
                        "path": "src/app.py",
                        "old": "def old_name():",
                        "new": "def new_name():",
                    },
                ),
            ),
            forbidden_tools=("bash_run",),
            threshold=0.8,
        )

        case = turn_to_test_case(turn, spec=spec)

        assert case.input == "Rename old_name to new_name"
        assert case.actual_output == "Done."
        assert [tool.name for tool in case.tools_called] == [
            "file_read",
            "file_replace",
        ]
        assert [tool.name for tool in case.expected_tools] == [
            "file_read",
            "file_replace",
        ]
        assert case.tools_called[0].output == "def old_name():\n    return 42\n"
        assert case.expected_tools[0].output is None
        assert case.metadata == {
            "task": "Rename old_name to new_name",
            "forbidden_tools": ["bash_run"],
            "threshold": 0.8,
        }

    def test_turn_without_final_output_becomes_empty_actual_output(self) -> None:
        turn = TurnTrace(
            user_input="Read file",
            tool_calls=(
                ToolCallRecord(
                    call_id="call-1",
                    name="file_read",
                    arguments={"path": "src/app.py"},
                    result_content="body",
                ),
            ),
            final_output=None,
        )
        spec = GoldenTurnSpec(task="Read file", expected_tools=())

        case = turn_to_test_case(turn, spec=spec)

        assert case.actual_output == ""
        assert case.expected_tools == ()


class TestGoldenFixtures:
    def test_load_tape_entries_reads_curated_parent_child_fixture(self) -> None:
        entries = load_tape_entries(FIXTURE_DIR / "parent-child-subagent-001.jsonl")

        assert len(entries) == 7
        assert entries[0].payload["content"] == "parent task"
        assert entries[2].meta["skip_context"] is True

    def test_load_golden_spec_reads_expected_tools_from_yaml(self) -> None:
        spec = load_golden_spec(FIXTURE_DIR / "parent-child-subagent-001.yaml")

        assert spec.task == "Run a child task and report back to the parent"
        assert [tool.name for tool in spec.expected_tools] == ["subagent"]
        assert spec.expected_tools[0].input_parameters == {"goal": "child task"}
        assert spec.forbidden_tools == ("bash_run",)
        assert spec.threshold == 1.0

    def test_build_test_cases_uses_visible_extraction_by_default(self) -> None:
        cases = build_test_cases(
            tape_path=FIXTURE_DIR / "parent-child-subagent-001.jsonl",
            spec_path=FIXTURE_DIR / "parent-child-subagent-001.yaml",
        )

        assert len(cases) == 1
        case = cases[0]
        assert case.input == "parent task"
        assert case.actual_output == "parent done"
        assert [tool.name for tool in case.tools_called] == ["subagent"]
        assert [tool.name for tool in case.expected_tools] == ["subagent"]
        assert case.tools_called[0].output == "Subagent completed: child done"

    def test_build_test_cases_rejects_multi_turn_tapes_in_v1(
        self, tmp_path: Path
    ) -> None:
        tape_path = tmp_path / "two-turns.jsonl"
        _ = tape_path.write_text(
            "\n".join(
                [
                    '{"id":"u1","kind":"message","payload":{"role":"user","content":"first"},"timestamp":1700000201.0}',
                    '{"id":"a1","kind":"message","payload":{"role":"assistant","content":"done one"},"timestamp":1700000202.0}',
                    '{"id":"u2","kind":"message","payload":{"role":"user","content":"second"},"timestamp":1700000203.0}',
                    '{"id":"a2","kind":"message","payload":{"role":"assistant","content":"done two"},"timestamp":1700000204.0}',
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="single-turn"):
            _ = build_test_cases(
                tape_path=tape_path,
                spec_path=FIXTURE_DIR / "parent-child-subagent-001.yaml",
            )
