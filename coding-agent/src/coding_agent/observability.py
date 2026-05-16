"""Coding Agent observability configuration and sink construction."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentkit.observability import NoopObservationSink, ObservationSink


def build_observation_sink(config: Mapping[str, Any]) -> ObservationSink | None:
    """Build the configured observation sink.

    The default is intentionally disabled. This module owns product-level
    exporter configuration; agentkit remains provider-neutral.
    """

    enabled = config.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("observability.enabled must be a boolean")
    if not enabled:
        return None

    backend = config.get("backend", "noop")
    if not isinstance(backend, str) or not backend.strip():
        raise ValueError("observability.backend must be a non-empty string")
    if backend != "noop":
        raise ValueError(f"unsupported observability backend: {backend}")
    return NoopObservationSink()
