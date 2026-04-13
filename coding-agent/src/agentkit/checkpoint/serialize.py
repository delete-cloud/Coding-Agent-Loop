from __future__ import annotations

import json
import logging
from typing import Any


logger = logging.getLogger(__name__)


def extract_serializable_states(plugin_states: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in plugin_states.items():
        try:
            serialized = json.dumps(value, allow_nan=False)
            result[key] = json.loads(serialized)
        except (TypeError, ValueError, OverflowError):
            logger.debug(
                "Skipping non-serializable checkpoint plugin state",
                extra={"state_key": key},
            )
            continue
    return result


def validate_json_safe(data: dict[str, Any], *, name: str) -> None:
    try:
        json.dumps(data, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be JSON-serializable, got error: {exc}") from exc
