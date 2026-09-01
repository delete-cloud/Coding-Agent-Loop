"""Deterministic parent projection rules for durable child facts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from coding_agent.stores.runtime_store import EventRecord


def include_in_parent_context(payload: Mapping[str, object]) -> bool:
    """Return whether one durable fact belongs in parent model context."""

    return not (
        payload.get("subagent_child") is True
        or payload.get("skip_parent_context") is True
    )


def build_parent_model_context_facts(
    facts: Iterable[EventRecord],
) -> tuple[EventRecord, ...]:
    """Filter durable child-internal facts from parent model context."""

    return tuple(fact for fact in facts if include_in_parent_context(fact.payload))


def include_in_parent_wire(
    event_kind: str,
    payload: Mapping[str, object],
) -> bool:
    """Return whether one durable fact belongs on the connected-chat parent wire."""

    is_child_internal = (
        payload.get("subagent_child") is True
        or payload.get("skip_parent_context") is True
    )
    if not is_child_internal:
        return True
    return (
        event_kind == "approval_requested"
        and isinstance(payload.get("target_run_id"), str)
        and bool(payload["target_run_id"])
        and isinstance(payload.get("target_parent_effect_id"), str)
        and bool(payload["target_parent_effect_id"])
    )
