"""Structured extraction from flat tape entry streams.

Resolves three problems that bite every consumer of raw tape data:

1. **Turn boundaries** — where does one user→agent exchange end and the
   next begin?  Complicated by child subagent entries injected mid-turn
   with ``meta.skip_context = True``.

2. **tool_call_id join** — ``tool_result`` entries persist only
   ``tool_call_id`` and ``content``, not the tool ``name``.  The extractor
   builds an index from ``tool_call`` entries to recover the full pairing.

3. **Batch tool calls** — parallel tool invocations produce N consecutive
   ``tool_call`` entries followed by N ``tool_result`` entries.  The
   extractor handles this interleaving correctly.

See ADR-0008 for the design rationale.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import cast

from agentkit._types import JsonDict
from agentkit.tape.models import Entry


class Visibility(Enum):
    """Controls which entries participate in extraction."""

    VISIBLE = "visible"
    """Skip entries with ``meta.skip_context == True`` (default).

    Prevents child subagent entries from splitting parent turns.
    """

    RAW = "raw"
    """Include all entries, even hidden sub-flows."""


@dataclass(frozen=True)
class ToolCallRecord:
    """A ``tool_call`` paired with its ``tool_result`` (if found).

    ``is_error`` is deliberately omitted — it is not persisted in the tape.
    Consumers can heuristically infer error status from ``result_content``
    if needed (e.g. check for ``"Error"`` / ``'"error"'`` prefixes).
    """

    call_id: str
    name: str
    arguments: JsonDict
    result_content: str | None = None


@dataclass(frozen=True)
class TurnTrace:
    """One user→agent exchange extracted from a tape.

    Attributes:
        user_input: The user message that started this turn.
        tool_calls: Ordered sequence of tool invocations in this turn.
        final_output: The last assistant text message in this turn,
            or ``None`` if the turn ended without one (e.g. max steps).
    """

    user_input: str
    tool_calls: tuple[ToolCallRecord, ...]
    final_output: str | None


def _is_visible(entry: Entry) -> bool:
    return not entry.meta.get("skip_context")


def _is_user_message(entry: Entry) -> bool:
    return entry.kind == "message" and entry.payload.get("role") == "user"


def _payload_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _payload_dict(payload: Mapping[str, object], key: str) -> JsonDict:
    value = payload.get(key)
    if isinstance(value, dict):
        return cast(JsonDict, value)
    return {}


def extract_turns(
    entries: Sequence[Entry],
    *,
    visibility: Visibility = Visibility.VISIBLE,
) -> list[TurnTrace]:
    """Walk *entries*, split at user messages, join tool_call ↔ tool_result.

    Parameters
    ----------
    entries:
        Typically from ``tape.snapshot()``.  Do **not** pass entries from
        ``TapeView.from_tape()`` — that applies windowing which loses
        historical steps needed for evaluation replay.
    visibility:
        ``VISIBLE`` (default) filters out ``skip_context`` entries for both
        boundary detection and content collection.  ``RAW`` includes
        everything.

    Returns
    -------
    list[TurnTrace]
        One ``TurnTrace`` per user message found.  Turns appear in
        chronological order.
    """
    include: Callable[[Entry], bool]
    if visibility is Visibility.VISIBLE:
        include = _is_visible
    else:
        include = lambda _entry: True

    # --- Phase 1: locate turn boundaries (indices of user messages) ---
    boundary_indices: list[int] = []
    for i, entry in enumerate(entries):
        if not include(entry):
            continue
        if _is_user_message(entry):
            boundary_indices.append(i)

    if not boundary_indices:
        return []

    # --- Phase 2: extract each turn ---
    turns: list[TurnTrace] = []
    for turn_idx, start in enumerate(boundary_indices):
        # The turn runs from this user message up to (but not including)
        # the next user message boundary, or end of entries.
        end = (
            boundary_indices[turn_idx + 1]
            if turn_idx + 1 < len(boundary_indices)
            else len(entries)
        )

        user_input = _payload_str(entries[start].payload, "content")

        # Collect tool_calls and build call_id → record index.
        call_id_to_index: dict[str, int] = {}
        records: list[ToolCallRecord] = []
        final_output: str | None = None

        for entry in entries[start + 1 : end]:
            if not include(entry):
                continue

            if entry.kind == "tool_call":
                call_id = _payload_str(entry.payload, "id")
                record = ToolCallRecord(
                    call_id=call_id,
                    name=_payload_str(entry.payload, "name"),
                    arguments=_payload_dict(entry.payload, "arguments"),
                )
                call_id_to_index[call_id] = len(records)
                records.append(record)

            elif entry.kind == "tool_result":
                tc_id = _payload_str(entry.payload, "tool_call_id")
                idx = call_id_to_index.get(tc_id)
                if idx is not None:
                    old = records[idx]
                    records[idx] = ToolCallRecord(
                        call_id=old.call_id,
                        name=old.name,
                        arguments=old.arguments,
                        # Empty or missing result content is normalized to None.
                        result_content=_payload_str(entry.payload, "content") or None,
                    )
                # If tc_id not found in index, the result belongs to a
                # call outside this turn (or from a child tape).  Drop it
                # silently — this is expected for subagent traces.

            elif (
                entry.kind == "message"
                and entry.payload.get("role") == "assistant"
                and entry.payload.get("content")
            ):
                # Track the *last* assistant text message in the turn.
                final_output = _payload_str(entry.payload, "content")

        turns.append(
            TurnTrace(
                user_input=user_input,
                tool_calls=tuple(records),
                final_output=final_output,
            )
        )

    return turns
