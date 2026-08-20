"""Console action, metadata, and tape-search helpers."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from fastapi import HTTPException

from coding_agent.stores.runtime_store import (
    AgentRunRecord,
)
from coding_agent.server.auth import (
    AuthContext,
)
from coding_agent.server.developer_console import (
    ConsoleActionSummary,
    ConsoleValidationOutcomeSummary,
    safe_id_value,
    safe_label_value,
)

from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME

from coding_agent.server.http.deps import (
    _auth_context_can_access_session,
    _get_visible_session,
)
from coding_agent.server.http.deps import _safe_dict

logger = logging.getLogger(LOGGER_NAME)


def _action_summary_from_item(
    run_id: str,
    raw_item: dict[str, object],
) -> ConsoleActionSummary | None:
    kind = safe_label_value(raw_item.get("kind") or raw_item.get("action_kind"))
    status = safe_label_value(raw_item.get("status") or raw_item.get("action_status"))
    if kind is None or status is None:
        return None
    policy = _safe_dict(raw_item.get("policy"))
    patch_summary = _safe_dict(raw_item.get("patch_summary") or raw_item.get("patch"))
    return ConsoleActionSummary(
        action_id=safe_id_value(raw_item.get("action_id") or raw_item.get("id")),
        run_id=run_id,
        interaction_id=safe_id_value(
            raw_item.get("interaction_id") or raw_item.get("approval_interaction_id")
        ),
        validation_id=safe_id_value(raw_item.get("validation_id")),
        kind=kind,
        status=status,
        policy_decision=safe_label_value(
            raw_item.get("policy_decision") or policy.get("decision")
        ),
        risk_level=safe_label_value(raw_item.get("risk_level")),
        changed_path_count=_optional_int(raw_item.get("changed_path_count")),
        extension_buckets=_safe_label_tuple(raw_item.get("file_extension_buckets")),
        approval_status=safe_label_value(raw_item.get("approval_status")),
        patch_summary=_safe_summary_pairs(
            patch_summary,
            (
                "changed_path_count",
                "created_count",
                "updated_count",
                "deleted_count",
                "hunk_count",
                "risk_level",
            ),
        ),
    )


def _validation_outcomes(
    report: dict[str, object],
) -> list[ConsoleValidationOutcomeSummary]:
    raw_outcomes = report.get("outcomes")
    if not isinstance(raw_outcomes, list):
        return []
    outcomes: list[ConsoleValidationOutcomeSummary] = []
    for raw_outcome in raw_outcomes:
        if not isinstance(raw_outcome, dict):
            continue
        label = safe_label_value(raw_outcome.get("label")) or "redacted"
        status = safe_label_value(raw_outcome.get("status"))
        if status is None:
            continue
        policy = _safe_dict(raw_outcome.get("policy"))
        failure = _safe_dict(raw_outcome.get("failure_summary"))
        outcomes.append(
            ConsoleValidationOutcomeSummary(
                label=label,
                status=status,
                exit_code=_optional_int(raw_outcome.get("exit_code")),
                duration_ms=_optional_int(raw_outcome.get("duration_ms")),
                policy_decision=safe_label_value(policy.get("decision")),
                failure_summary=_safe_failure_summary_pairs(failure),
            )
        )
    return outcomes


def _metadata_lists(
    metadata: dict[str, object],
    keys: tuple[str, ...],
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
    return items


def _first_metadata_item(
    metadata: dict[str, object],
    keys: tuple[str, ...],
) -> dict[str, object] | None:
    for item in _metadata_lists(metadata, keys):
        return item
    return None


def _safe_observability_config() -> dict[str, object]:
    try:
        return _bindings.module()._load_observability_config()
    except Exception:
        logger.exception("Unable to load observability config for console")
        return {}


def _safe_observability_link(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"}:
        return None
    if parts.username or parts.password or parts.query or parts.fragment:
        return None
    if not parts.netloc:
        return None
    return parts.geturl()


def _safe_label_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        raw_items: list[object] = [item.strip() for item in value.split(",")]
    elif isinstance(value, list | tuple):
        raw_items = list(value)
    else:
        return ()
    labels: list[str] = []
    for item in raw_items:
        label = safe_label_value(item)
        if label is not None:
            labels.append(label)
    return tuple(labels)


def _safe_summary_pairs(
    mapping: dict[str, object],
    allowed_keys: tuple[str, ...],
) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for key in allowed_keys:
        value = mapping.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            pairs.append((key, str(value).lower()))
        elif isinstance(value, int | float | str):
            safe_value = safe_label_value(str(value))
            if safe_value is not None:
                pairs.append((key, safe_value))
    return tuple(pairs)


def _safe_failure_summary_pairs(
    mapping: dict[str, object],
) -> tuple[tuple[str, str], ...]:
    display_names = {
        "stdout_bytes": "output_bytes",
        "stderr_bytes": "error_bytes",
        "stdout_lines": "output_lines",
        "stderr_lines": "error_lines",
        "timeout_seconds": "timeout_seconds",
        "policy_decision": "policy_decision",
        "error_kind": "error_kind",
    }
    pairs: list[tuple[str, str]] = []
    for key, display_name in display_names.items():
        value = mapping.get(key)
        if isinstance(value, int | float):
            pairs.append((display_name, str(value)))
        elif isinstance(value, str):
            safe_value = safe_label_value(value)
            if safe_value is not None:
                pairs.append((display_name, safe_value))
    return tuple(pairs)


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


async def _visible_console_tape_ids(
    auth_context: AuthContext | None,
) -> set[str]:
    if auth_context is None or auth_context.scope == "admin":
        return set()
    visible: set[str] = set()
    for session_id in await _bindings.module().session_manager.list_sessions_async():
        try:
            session = await _bindings.module().session_manager.get_session_async(
                session_id
            )
        except KeyError:
            continue
        if not _auth_context_can_access_session(auth_context, session):
            continue
        if session.tape_id is not None:
            visible.add(session.tape_id)
        try:
            runs = await _bindings.module().session_manager.list_runtime_runs(
                session_id
            )
        except RuntimeError:
            runs = []
        for run in runs:
            if run.tape_id is not None:
                visible.add(run.tape_id)
    return visible


def _can_search_tape(
    *,
    auth_context: AuthContext | None,
    tape_id: str | None,
    run_id: str | None,
    visible_tape_ids: set[str],
) -> bool:
    if auth_context is None or auth_context.scope == "admin":
        return True
    if run_id is not None and tape_id is None:
        return False
    if tape_id is None:
        return True
    return tape_id in visible_tape_ids


async def _get_visible_runtime_run(
    run_id: str,
    auth_context: AuthContext | None,
) -> AgentRunRecord:
    try:
        record = await _bindings.module().session_manager.load_runtime_run(run_id)
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail="Runtime run not found") from exc

    try:
        await _get_visible_session(record.session_id, auth_context)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail="Runtime run not found",
            ) from exc
        raise
    return record


__all__ = [
    "_action_summary_from_item",
    "_can_search_tape",
    "_first_metadata_item",
    "_get_visible_runtime_run",
    "_metadata_lists",
    "_optional_int",
    "_safe_failure_summary_pairs",
    "_safe_label_tuple",
    "_safe_observability_config",
    "_safe_observability_link",
    "_safe_summary_pairs",
    "_validation_outcomes",
    "_visible_console_tape_ids",
]
