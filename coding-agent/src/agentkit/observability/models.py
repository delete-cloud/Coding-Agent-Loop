"""Provider-neutral observability domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time
from typing import Any, Literal

ObservationStatus = Literal["ok", "error"]
ObservationAttributes = dict[str, Any]


@dataclass(frozen=True)
class ObservationEvent:
    """Point-in-time observation event."""

    name: str
    attributes: ObservationAttributes = field(default_factory=dict)
    timestamp: float = field(default_factory=time)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", dict(self.attributes))


@dataclass(frozen=True)
class SpanRecord:
    """Completed span record emitted by agentkit instrumentation.

    ``span_id`` and ``parent_span_id`` are provider-neutral OpenTelemetry span
    identifiers (hex strings). They are optional: when unset, exporters mint a
    fresh span id and treat the span as a trace root. When set, exporters emit
    them as the OTLP ``spanId``/``parentSpanId`` so backends can render the
    parent/child observation tree.
    """

    name: str
    status: ObservationStatus
    attributes: ObservationAttributes = field(default_factory=dict)
    start_time: float | None = None
    end_time: float | None = None
    duration_ms: float = 0.0
    error_type: str | None = None
    error_message: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", dict(self.attributes))
