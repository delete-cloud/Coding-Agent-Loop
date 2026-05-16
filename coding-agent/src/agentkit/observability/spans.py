"""Span recording helpers for agentkit observability."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter, time
from typing import Any

from agentkit.observability.models import ObservationAttributes, SpanRecord
from agentkit.observability.sinks import NoopObservationSink, ObservationSink

MAX_ERROR_MESSAGE_CHARS = 500


@dataclass
class ActiveSpan:
    """Mutable span context yielded while observed work is running."""

    name: str
    attributes: ObservationAttributes = field(default_factory=dict)

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value


def _safe_record_span(sink: ObservationSink, span: SpanRecord) -> None:
    try:
        sink.record_span(span)
    except Exception:  # noqa: BLE001 - sink failures must not affect business logic
        return


def _error_message(exc: BaseException) -> str:
    message = str(exc)
    if len(message) <= MAX_ERROR_MESSAGE_CHARS:
        return message
    return message[: MAX_ERROR_MESSAGE_CHARS - 3].rstrip() + "..."


@contextmanager
def record_span(
    name: str,
    *,
    sink: ObservationSink | None = None,
    attributes: Mapping[str, Any] | None = None,
) -> Iterator[ActiveSpan]:
    """Record one completed span without letting sink failures affect work."""

    active = ActiveSpan(name=name, attributes=dict(attributes or {}))
    span_sink = sink if sink is not None else NoopObservationSink()
    start_time = time()
    start_counter = perf_counter()
    try:
        yield active
    except Exception as exc:
        end_time = time()
        duration_ms = (perf_counter() - start_counter) * 1000
        _safe_record_span(
            span_sink,
            SpanRecord(
                name=name,
                status="error",
                attributes=active.attributes,
                start_time=start_time,
                end_time=end_time,
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
                error_message=_error_message(exc),
            ),
        )
        raise
    else:
        end_time = time()
        duration_ms = (perf_counter() - start_counter) * 1000
        _safe_record_span(
            span_sink,
            SpanRecord(
                name=name,
                status="ok",
                attributes=active.attributes,
                start_time=start_time,
                end_time=end_time,
                duration_ms=duration_ms,
            ),
        )
