"""Tests for adapter_types module."""

import pytest
from coding_agent.adapter_types import TurnOutcome, StopReason


class TestStopReasonEnum:
    """Test StopReason enum."""

    def test_stop_reason_has_required_values(self):
        """Test that StopReason has all required enum values."""
        assert hasattr(StopReason, "NO_TOOL_CALLS")
        assert hasattr(StopReason, "MAX_STEPS_REACHED")
        assert hasattr(StopReason, "DOOM_LOOP")
        assert hasattr(StopReason, "ERROR")

    def test_stop_reason_values(self):
        """Test that StopReason enum values are correct."""
        assert StopReason.NO_TOOL_CALLS.value == "no_tool_calls"
        assert StopReason.MAX_STEPS_REACHED.value == "max_steps_reached"
        assert StopReason.DOOM_LOOP.value == "doom_loop"
        assert StopReason.ERROR.value == "error"


class TestTurnOutcomeDataclass:
    """Test TurnOutcome dataclass."""

    def test_turn_outcome_has_required_fields(self):
        """Test that TurnOutcome has all required fields."""
        outcome = TurnOutcome(
            stop_reason=StopReason.NO_TOOL_CALLS,
            final_message="done",
            steps_taken=5,
            error=None,
        )
        assert hasattr(outcome, "stop_reason")
        assert hasattr(outcome, "final_message")
        assert hasattr(outcome, "steps_taken")
        assert hasattr(outcome, "error")

    def test_turn_outcome_field_types(self):
        """Test that TurnOutcome fields have correct types."""
        outcome = TurnOutcome(
            stop_reason=StopReason.MAX_STEPS_REACHED,
            final_message="max steps",
            steps_taken=10,
            error=None,
        )
        assert isinstance(outcome.stop_reason, StopReason)
        assert isinstance(outcome.final_message, str)
        assert isinstance(outcome.steps_taken, int)
        assert outcome.error is None

    def test_turn_outcome_with_error(self):
        """Test TurnOutcome with error field populated."""
        outcome = TurnOutcome(
            stop_reason=StopReason.ERROR,
            final_message=None,
            steps_taken=3,
            error="Something went wrong",
        )
        assert outcome.stop_reason == StopReason.ERROR
        assert outcome.final_message is None
        assert outcome.steps_taken == 3
        assert isinstance(outcome.error, str)

    def test_turn_outcome_default_values(self):
        """Test TurnOutcome default values."""
        outcome = TurnOutcome(stop_reason=StopReason.NO_TOOL_CALLS)
        assert outcome.stop_reason == StopReason.NO_TOOL_CALLS
        assert outcome.final_message is None
        assert outcome.steps_taken == 0
        assert outcome.error is None

    def test_turn_outcome_doom_loop(self):
        """Test TurnOutcome with DOOM_LOOP stop reason."""
        outcome = TurnOutcome(
            stop_reason=StopReason.DOOM_LOOP,
            final_message="Loop detected",
            steps_taken=100,
            error="Detected circular tool calls",
        )
        assert outcome.stop_reason == StopReason.DOOM_LOOP
        assert outcome.steps_taken == 100
