"""Tests for agentkit.result reducers and provider-neutral result refs."""

from agentkit.result.models import (
    ArtifactRef,
    FailureSummary,
    ResultRef,
)
from agentkit.result.reducers import result_from_turn_trace
from agentkit.tape.extract import ToolCallRecord, TurnTrace


def test_turn_result_from_trace_preserves_final_output() -> None:
    turn = TurnTrace(
        user_input="write a summary",
        tool_calls=(),
        final_output="Summary complete.",
    )

    result = result_from_turn_trace(turn)

    assert result.final_output == "Summary complete."
    assert result.verification_summary is None


def test_turn_result_from_trace_summarizes_tool_activity() -> None:
    turn = TurnTrace(
        user_input="inspect files",
        tool_calls=(
            ToolCallRecord(
                call_id="tc1",
                name="file_read",
                arguments={"path": "src/app.py"},
                result_content="contents",
            ),
            ToolCallRecord(
                call_id="tc2",
                name="bash_run",
                arguments={"command": "uv run pytest tests/unit -q"},
                result_content="passed",
            ),
        ),
        final_output="Done.",
    )

    result = result_from_turn_trace(turn)

    assert result.verification_summary is not None
    assert (
        result.verification_summary.summary == "Tool activity: file_read: src/app.py; "
        "bash_run: uv run pytest tests/unit -q"
    )
    assert result.verification_summary.tool_names == ("file_read", "bash_run")


def test_turn_result_from_trace_omits_verification_without_tools() -> None:
    turn = TurnTrace(
        user_input="answer directly",
        tool_calls=(),
        final_output="Direct answer.",
    )

    result = result_from_turn_trace(turn)

    assert result.verification_summary is None


def test_turn_result_from_trace_limits_tool_summary_items() -> None:
    turn = TurnTrace(
        user_input="many steps",
        tool_calls=tuple(
            ToolCallRecord(
                call_id=f"tc{i}",
                name=f"tool_{i}",
                arguments={"path": f"file_{i}.py"},
            )
            for i in range(7)
        ),
        final_output="Done.",
    )

    result = result_from_turn_trace(turn)

    assert result.verification_summary is not None
    assert result.verification_summary.summary.endswith("; +2 more")
    assert result.verification_summary.tool_names == (
        "tool_0",
        "tool_1",
        "tool_2",
        "tool_3",
        "tool_4",
    )


def test_failure_summary_accepts_host_application_details() -> None:
    failure = FailureSummary(
        message="Session turn failed.",
        details="Provider returned a 500 response.",
        retryable=True,
        metadata={"provider": "test-provider"},
    )

    assert failure.message == "Session turn failed."
    assert failure.details == "Provider returned a 500 response."
    assert failure.retryable is True
    assert failure.metadata == {"provider": "test-provider"}


def test_artifact_ref_accepts_provider_neutral_metadata() -> None:
    artifact = ArtifactRef(
        artifact_id="artifact_patch_1",
        kind="patch",
        title="Workspace patch",
        summary="Adds two files",
        uri="agentkit://artifacts/artifact_patch_1",
        metadata={"line_count": 42},
        producer_turn_id="turn_1",
    )

    assert artifact.kind == "patch"
    assert artifact.metadata == {"line_count": 42}
    assert artifact.producer_turn_id == "turn_1"


def test_result_ref_distinguishes_logical_result_from_artifact() -> None:
    result_ref = ResultRef(
        result_id="result_session_1",
        kind="session_result",
        session_id="session_1",
        turn_id="turn_2",
        label="Latest session result",
        artifact_ids=("artifact_patch_1",),
        result_ids=("result_turn_2",),
        metadata={"status": "completed"},
    )

    assert result_ref.kind == "session_result"
    assert result_ref.artifact_ids == ("artifact_patch_1",)
    assert result_ref.result_ids == ("result_turn_2",)
    assert result_ref.metadata == {"status": "completed"}
