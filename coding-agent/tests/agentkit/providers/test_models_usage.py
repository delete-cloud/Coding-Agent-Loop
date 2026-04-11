"""Tests for UsageEvent — provider-reported token usage data."""

from __future__ import annotations

import dataclasses

import pytest

from agentkit.providers.models import StreamEvent, UsageEvent


class TestUsageEventCreation:
    """UsageEvent creation with explicit and default values."""

    def test_creation_with_explicit_tokens(self) -> None:
        event = UsageEvent(input_tokens=100, output_tokens=50)
        assert event.input_tokens == 100
        assert event.output_tokens == 50

    def test_creation_with_defaults(self) -> None:
        event = UsageEvent()
        assert event.input_tokens == 0
        assert event.output_tokens == 0
        assert event.provider_name == ""

    def test_creation_with_provider_name(self) -> None:
        event = UsageEvent(input_tokens=200, output_tokens=80, provider_name="openai")
        assert event.provider_name == "openai"

    def test_kind_is_usage(self) -> None:
        event = UsageEvent()
        assert event.kind == "usage"

    def test_kind_not_settable_via_init(self) -> None:
        """kind field has init=False, so it should not be accepted as an argument."""
        with pytest.raises(TypeError):
            UsageEvent(kind="custom")  # type: ignore[call-arg]


class TestUsageEventInheritance:
    """UsageEvent is a proper StreamEvent subclass."""

    def test_is_stream_event(self) -> None:
        event = UsageEvent(input_tokens=10, output_tokens=5)
        assert isinstance(event, StreamEvent)

    def test_has_kind_attribute(self) -> None:
        event = UsageEvent()
        assert hasattr(event, "kind")


class TestUsageEventImmutability:
    """UsageEvent is frozen (immutable)."""

    def test_cannot_mutate_input_tokens(self) -> None:
        event = UsageEvent(input_tokens=100, output_tokens=50)
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.input_tokens = 200  # type: ignore[misc]

    def test_cannot_mutate_output_tokens(self) -> None:
        event = UsageEvent(input_tokens=100, output_tokens=50)
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.output_tokens = 999  # type: ignore[misc]

    def test_cannot_mutate_provider_name(self) -> None:
        event = UsageEvent(provider_name="openai")
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.provider_name = "anthropic"  # type: ignore[misc]

    def test_cannot_mutate_kind(self) -> None:
        event = UsageEvent()
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.kind = "other"  # type: ignore[misc]
