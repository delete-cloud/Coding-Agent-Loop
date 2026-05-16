"""Observation sink protocols and no-op implementation."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentkit.observability.models import ObservationEvent, SpanRecord


@runtime_checkable
class ObservationSink(Protocol):
    """Protocol for metadata-only observation sinks."""

    def record_span(self, span: SpanRecord) -> None: ...
    def record_event(self, event: ObservationEvent) -> None: ...


class NoopObservationSink:
    """Observation sink that intentionally drops all records."""

    def record_span(self, span: SpanRecord) -> None:
        del span

    def record_event(self, event: ObservationEvent) -> None:
        del event
