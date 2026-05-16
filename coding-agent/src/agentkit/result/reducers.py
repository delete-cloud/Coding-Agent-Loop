"""Reducers from tape-derived traces to provider-neutral result models."""

from __future__ import annotations

from agentkit.result.models import TurnResult, VerificationSummary
from agentkit.tape.extract import ToolCallRecord, TurnTrace

TOOL_DETAIL_KEYS = (
    "command",
    "path",
    "file_path",
    "pattern",
)
MAX_TOOL_ITEMS = 5
MAX_TOOL_DETAIL_CHARS = 160


def _compact_text(text: str, *, max_chars: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _tool_detail(call: ToolCallRecord) -> str | None:
    for key in TOOL_DETAIL_KEYS:
        value = call.arguments.get(key)
        if not isinstance(value, str) or value.strip() == "":
            continue
        return _compact_text(value, max_chars=MAX_TOOL_DETAIL_CHARS)
    return None


def verification_summary_from_turn_trace(
    turn: TurnTrace,
) -> VerificationSummary | None:
    """Summarize tool activity from a turn trace, if any tools ran."""

    if not turn.tool_calls:
        return None

    items: list[str] = []
    tool_names: list[str] = []
    for call in turn.tool_calls[:MAX_TOOL_ITEMS]:
        name = call.name.strip() or "unnamed_tool"
        tool_names.append(name)
        detail = _tool_detail(call)
        if detail is None:
            items.append(name)
        else:
            items.append(f"{name}: {detail}")

    remaining = len(turn.tool_calls) - len(items)
    if remaining > 0:
        items.append(f"+{remaining} more")

    return VerificationSummary(
        summary="Tool activity: " + "; ".join(items),
        tool_names=tuple(tool_names),
    )


def result_from_turn_trace(turn: TurnTrace) -> TurnResult:
    """Build a provider-neutral turn result from a tape turn trace."""

    return TurnResult(
        final_output=turn.final_output,
        verification_summary=verification_summary_from_turn_trace(turn),
    )


latest_turn_result = result_from_turn_trace
