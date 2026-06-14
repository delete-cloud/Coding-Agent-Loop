from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Protocol, cast

from agentkit.checkpoint.models import CheckpointMeta
from agentkit.tape.models import Anchor

from coding_agent.stores.runtime_store import (
    AgentRunRecord,
    JSONObject,
    RunMessageSnapshotRecord,
)


RESUME_BOUNDARY_PRODUCT_ANCHOR_TYPE = "resume_boundary"
RESUME_CONTEXT_STRATEGY = "checkpoint+tape_tail+message_snapshot"
DEFAULT_RESUME_PROMPT = "Continue from the last known state."
RESUME_TAPE_TAIL_LIMIT = 5
RESUME_CONTEXT_JSON_LIMIT = 4000
RESUME_PLAN_NOTE_LIMIT = 1200


class RuntimeResumeSession(Protocol):
    id: str
    tape_id: str | None


class RuntimeResumeOrchestrationSession(RuntimeResumeSession, Protocol):
    turn_in_progress: bool
    turn_status: str


CheckpointLister = Callable[[str], Awaitable[list[CheckpointMeta]]]
TapeEntryLoader = Callable[[str], Awaitable[list[Mapping[str, object]]]]
MessageSnapshotLoader = Callable[[str], Awaitable[RunMessageSnapshotRecord | None]]
TapeEntrySaver = Callable[[str, list[Mapping[str, object]]], Awaitable[None]]
RuntimeRunLoader = Callable[[str], Awaitable[AgentRunRecord | None]]
RuntimeSessionPersister = Callable[[RuntimeResumeOrchestrationSession], Awaitable[None]]
RuntimeSessionAttachedPredicate = Callable[[RuntimeResumeOrchestrationSession], bool]
RuntimeLiveBoundaryAnchorAppender = Callable[
    [RuntimeResumeOrchestrationSession, Anchor], None
]
RuntimeRunIdFactory = Callable[[], str]
RuntimeResumeStoreRequirement = Callable[[], object]
RuntimeResumeOwnerAsserter = Callable[[str], Awaitable[None]]
RuntimeResumeSessionLoader = Callable[
    [str],
    Awaitable[RuntimeResumeOrchestrationSession],
]


@dataclass(frozen=True)
class RuntimeResumeContext:
    previous_run_id: str
    resume_from_run_id: str
    resume_from_event_id: str | None
    resume_reason: str
    checkpoint_count: int = 0
    latest_checkpoint_id: str | None = None
    latest_checkpoint_label: str | None = None
    tape_entry_count: int = 0
    tape_tail: tuple[JSONObject, ...] = ()
    latest_plan_note: str | None = None
    latest_message_snapshot_id: str | None = None
    latest_message_snapshot_message_count: int | None = None
    resume_boundary_anchor_id: str | None = None
    resume_context_strategy: str = RESUME_CONTEXT_STRATEGY

    def metadata(self) -> JSONObject:
        metadata: JSONObject = {
            "previous_run_id": self.previous_run_id,
            "resume_from_run_id": self.resume_from_run_id,
            "resume_reason": self.resume_reason,
            "resume_context_injected": True,
            "resume_context_strategy": self.resume_context_strategy,
            "checkpoint_count": self.checkpoint_count,
            "tape_entry_count": self.tape_entry_count,
            "resume_tape_tail_entry_count": len(self.tape_tail),
            "resume_plan_note_included": self.latest_plan_note is not None,
        }
        if self.resume_from_event_id is not None:
            metadata["resume_from_event_id"] = self.resume_from_event_id
        if self.latest_checkpoint_id is not None:
            metadata["latest_checkpoint_id"] = self.latest_checkpoint_id
        if self.latest_checkpoint_label is not None:
            metadata["latest_checkpoint_label"] = self.latest_checkpoint_label
        if self.latest_message_snapshot_id is not None:
            metadata["latest_message_snapshot_id"] = self.latest_message_snapshot_id
        if self.latest_message_snapshot_message_count is not None:
            metadata["latest_message_snapshot_message_count"] = (
                self.latest_message_snapshot_message_count
            )
        if self.resume_boundary_anchor_id is not None:
            metadata["resume_boundary_anchor_id"] = self.resume_boundary_anchor_id
            metadata["resume_boundary_anchor_type"] = (
                RESUME_BOUNDARY_PRODUCT_ANCHOR_TYPE
            )
        return metadata


RuntimeRunDispatcher = Callable[
    [str, str, str, RuntimeResumeContext], Awaitable[AgentRunRecord | None]
]
AttachedRuntimeRunRequester = Callable[
    [str, str, str, RuntimeResumeContext], Awaitable[AgentRunRecord]
]


@dataclass(frozen=True)
class RuntimeResumeOrchestrationService:
    resume_service: RuntimeResumeService
    latest_runtime_run: Callable[[str], Awaitable[AgentRunRecord | None]]
    latest_runtime_event_id: Callable[[AgentRunRecord], Awaitable[str | None]]
    load_runtime_run: RuntimeRunLoader
    persist_session: RuntimeSessionPersister
    list_checkpoints: CheckpointLister
    load_tape_entries: TapeEntryLoader
    save_tape_entries: TapeEntrySaver
    load_message_snapshot: MessageSnapshotLoader
    run_local: RuntimeRunDispatcher
    request_attached: AttachedRuntimeRunRequester
    session_is_attached: RuntimeSessionAttachedPredicate
    append_live_boundary_anchor: RuntimeLiveBoundaryAnchorAppender
    active_resume_blocking_statuses: frozenset[str]
    run_id_factory: RuntimeRunIdFactory = lambda: uuid.uuid4().hex

    async def resume(
        self,
        session: RuntimeResumeOrchestrationSession,
        *,
        prompt: str | None = None,
        resume_reason: str = "user_resume",
    ) -> AgentRunRecord:
        if not resume_reason.strip():
            raise ValueError("resume_reason must be non-empty")
        if session.turn_in_progress or session.turn_status in {
            "running",
            "cancelling",
        }:
            raise RuntimeError("turn already in progress")

        previous_run = await self.latest_runtime_run(session.id)
        if previous_run is None:
            raise RuntimeError("session has no previous run to resume")
        if previous_run.status in self.active_resume_blocking_statuses:
            raise RuntimeError("latest run is still active")
        if session.tape_id is None and previous_run.tape_id is not None:
            session.tape_id = previous_run.tape_id
            await self.persist_session(session)

        resume_context = await self.resume_service.build_context(
            session=session,
            previous_run=previous_run,
            resume_from_event_id=await self.latest_runtime_event_id(previous_run),
            resume_reason=resume_reason,
            list_checkpoints=self.list_checkpoints,
            load_tape_entries=self.load_tape_entries,
            load_message_snapshot=self.load_message_snapshot,
        )
        resume_context = await self.append_boundary_anchor(session, resume_context)
        resume_prompt = self.resume_service.resume_prompt(
            resume_context,
            prompt=prompt,
        )
        run_id = self.run_id_factory()
        if self.session_is_attached(session):
            return await self.request_attached(
                session.id,
                resume_prompt,
                run_id,
                resume_context,
            )

        record = await self.run_local(
            session.id,
            resume_prompt,
            run_id,
            resume_context,
        )
        if record is not None:
            return record
        record = await self.load_runtime_run(run_id)
        if record is None:
            raise RuntimeError(f"resumed runtime run was not recorded: {run_id}")
        return record

    async def append_boundary_anchor(
        self,
        session: RuntimeResumeOrchestrationSession,
        resume_context: RuntimeResumeContext,
    ) -> RuntimeResumeContext:
        if not session.tape_id:
            raise RuntimeError("session tape_id is required to append resume boundary")
        anchor = self.resume_service.resume_boundary_anchor(resume_context)
        await self.save_tape_entries(session.tape_id, [anchor.to_dict()])
        self.append_live_boundary_anchor(session, anchor)
        return self.resume_service.bind_boundary_anchor(
            resume_context,
            anchor_id=anchor.id,
        )


@dataclass(frozen=True)
class RuntimeResumeSessionOrchestrationService:
    require_runtime_store: RuntimeResumeStoreRequirement
    assert_owner: RuntimeResumeOwnerAsserter
    load_session: RuntimeResumeSessionLoader
    resume_orchestration: RuntimeResumeOrchestrationService

    async def resume_session(
        self,
        session_id: str,
        *,
        prompt: str | None = None,
        resume_reason: str = "user_resume",
    ) -> AgentRunRecord:
        self.require_runtime_store()
        await self.assert_owner(session_id)
        session = await self.load_session(session_id)
        return await self.resume_orchestration.resume(
            session=session,
            prompt=prompt,
            resume_reason=resume_reason,
        )


@dataclass(frozen=True)
class RuntimeResumeService:
    async def build_context(
        self,
        *,
        session: RuntimeResumeSession,
        previous_run: AgentRunRecord,
        resume_from_event_id: str | None,
        resume_reason: str,
        list_checkpoints: CheckpointLister,
        load_tape_entries: TapeEntryLoader,
        load_message_snapshot: MessageSnapshotLoader,
    ) -> RuntimeResumeContext:
        checkpoint_context = await self.latest_checkpoint_context(
            session,
            list_checkpoints=list_checkpoints,
        )
        tape_context = await self.latest_tape_context(
            session,
            load_tape_entries=load_tape_entries,
        )
        message_snapshot_context = await self.latest_message_snapshot_context(
            previous_run,
            load_message_snapshot=load_message_snapshot,
        )
        return RuntimeResumeContext(
            previous_run_id=previous_run.run_id,
            resume_from_run_id=previous_run.run_id,
            resume_from_event_id=resume_from_event_id,
            resume_reason=resume_reason,
            checkpoint_count=checkpoint_context["checkpoint_count"],
            latest_checkpoint_id=checkpoint_context["latest_checkpoint_id"],
            latest_checkpoint_label=checkpoint_context["latest_checkpoint_label"],
            tape_entry_count=tape_context["tape_entry_count"],
            tape_tail=tape_context["tape_tail"],
            latest_plan_note=tape_context["latest_plan_note"],
            latest_message_snapshot_id=message_snapshot_context[
                "latest_message_snapshot_id"
            ],
            latest_message_snapshot_message_count=message_snapshot_context[
                "latest_message_snapshot_message_count"
            ],
        )

    async def latest_checkpoint_context(
        self,
        session: RuntimeResumeSession,
        *,
        list_checkpoints: CheckpointLister,
    ) -> dict[str, object]:
        if session.tape_id is None:
            return {
                "checkpoint_count": 0,
                "latest_checkpoint_id": None,
                "latest_checkpoint_label": None,
            }
        checkpoints = await list_checkpoints(session.id)
        if not checkpoints:
            return {
                "checkpoint_count": 0,
                "latest_checkpoint_id": None,
                "latest_checkpoint_label": None,
            }
        latest_checkpoint = max(
            checkpoints,
            key=lambda checkpoint: (checkpoint.created_at, checkpoint.checkpoint_id),
        )
        return {
            "checkpoint_count": len(checkpoints),
            "latest_checkpoint_id": latest_checkpoint.checkpoint_id,
            "latest_checkpoint_label": latest_checkpoint.label,
        }

    async def latest_tape_context(
        self,
        session: RuntimeResumeSession,
        *,
        load_tape_entries: TapeEntryLoader,
    ) -> dict[str, object]:
        if session.tape_id is None:
            return {
                "tape_entry_count": 0,
                "tape_tail": (),
                "latest_plan_note": None,
            }
        entries = await load_tape_entries(session.tape_id)
        tail_entries = entries[-RESUME_TAPE_TAIL_LIMIT:]
        tape_tail = tuple(_resume_tape_entry_summary(entry) for entry in tail_entries)
        return {
            "tape_entry_count": len(entries),
            "tape_tail": tape_tail,
            "latest_plan_note": _latest_plan_note_from_tape_tail(tape_tail),
        }

    async def latest_message_snapshot_context(
        self,
        previous_run: AgentRunRecord,
        *,
        load_message_snapshot: MessageSnapshotLoader,
    ) -> dict[str, object]:
        snapshot_id = f"{previous_run.run_id}:latest"
        snapshot = await load_message_snapshot(snapshot_id)
        if snapshot is None:
            return {
                "latest_message_snapshot_id": None,
                "latest_message_snapshot_message_count": None,
            }
        return {
            "latest_message_snapshot_id": snapshot.snapshot_id,
            "latest_message_snapshot_message_count": len(snapshot.messages),
        }

    def resume_prompt(
        self,
        resume_context: RuntimeResumeContext,
        *,
        prompt: str | None,
    ) -> str:
        return _resume_prompt(resume_context, prompt=prompt)

    def resume_boundary_anchor(
        self,
        resume_context: RuntimeResumeContext,
    ) -> Anchor:
        return Anchor(
            anchor_type="context",
            payload={"label": "Resume boundary"},
            meta=_resume_boundary_anchor_meta(resume_context),
        )

    def bind_boundary_anchor(
        self,
        resume_context: RuntimeResumeContext,
        *,
        anchor_id: str,
    ) -> RuntimeResumeContext:
        return replace(resume_context, resume_boundary_anchor_id=anchor_id)


def _truncate_resume_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: limit - 14]}...[truncated]"


def _compact_resume_json(
    value: object, *, limit: int = RESUME_CONTEXT_JSON_LIMIT
) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return _truncate_resume_text(rendered, limit)


def _resume_tape_entry_summary(entry: Mapping[str, object]) -> JSONObject:
    kind = entry.get("kind")
    if not isinstance(kind, str) or not kind:
        raise TypeError("tape entry kind must be a non-empty string")
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        raise TypeError("tape entry payload must be a JSON object")
    summary: JSONObject = {
        "kind": kind,
        "payload": cast(JSONObject, payload),
    }
    entry_id = entry.get("id")
    if isinstance(entry_id, str) and entry_id:
        summary["id"] = entry_id
    meta = entry.get("meta")
    if isinstance(meta, dict) and meta:
        summary["meta"] = cast(JSONObject, meta)
    anchor_type = entry.get("anchor_type")
    if isinstance(anchor_type, str) and anchor_type:
        summary["anchor_type"] = anchor_type
    return summary


def _entry_has_plan_signal(kind: str, payload: Mapping[str, object]) -> bool:
    kind_lower = kind.lower()
    if "plan" in kind_lower or "todo" in kind_lower:
        return True
    for key in payload:
        key_lower = key.lower()
        if key_lower in {"plan", "tasks", "todos"} or "plan" in key_lower:
            return True
    for key in ("tool_name", "name", "function", "tool"):
        value = payload.get(key)
        if isinstance(value, str) and value.lower() in {
            "plan",
            "planner",
            "todo_read",
            "todo_write",
        }:
            return True
    rendered = _compact_resume_json(payload, limit=RESUME_PLAN_NOTE_LIMIT).lower()
    return (
        "todo_write" in rendered
        or "todo_read" in rendered
        or "current plan" in rendered
    )


def _latest_plan_note_from_tape_tail(
    tape_tail: tuple[JSONObject, ...],
) -> str | None:
    for entry in reversed(tape_tail):
        kind = entry.get("kind")
        payload = entry.get("payload")
        if not isinstance(kind, str) or not isinstance(payload, dict):
            continue
        if not _entry_has_plan_signal(kind, payload):
            continue
        return _compact_resume_json(entry, limit=RESUME_PLAN_NOTE_LIMIT)
    return None


def _resume_prompt(
    resume_context: RuntimeResumeContext,
    *,
    prompt: str | None,
) -> str:
    user_prompt = (
        prompt if prompt is not None and prompt.strip() else DEFAULT_RESUME_PROMPT
    )
    event_line = (
        f"Last known event id: {resume_context.resume_from_event_id}."
        if resume_context.resume_from_event_id is not None
        else "No runtime events were recorded for the previous run."
    )
    checkpoint_line = _resume_checkpoint_line(resume_context)
    message_snapshot_line = _resume_message_snapshot_line(resume_context)
    tape_tail_lines = _resume_tape_tail_lines(resume_context)
    plan_note_lines = _resume_plan_note_lines(resume_context)
    return "\n".join(
        [
            "Previous run was interrupted.",
            f"Previous run id: {resume_context.previous_run_id}.",
            f"Resume from run id: {resume_context.resume_from_run_id}.",
            event_line,
            checkpoint_line,
            message_snapshot_line,
            "Resume continues from current session history; it does not restore or roll back to a checkpoint.",
            *tape_tail_lines,
            *plan_note_lines,
            "Continue from the last known state.",
            "Do not repeat completed work unless needed.",
            "",
            "User resume request:",
            user_prompt,
        ]
    )


def _resume_message_snapshot_line(resume_context: RuntimeResumeContext) -> str:
    if resume_context.latest_message_snapshot_id is None:
        return "No runtime message snapshot is available for the previous run."
    count = resume_context.latest_message_snapshot_message_count
    count_text = f" ({count} messages)" if count is not None else ""
    return (
        "Latest runtime message snapshot: "
        f"{resume_context.latest_message_snapshot_id}{count_text}."
    )


def _resume_tape_tail_lines(resume_context: RuntimeResumeContext) -> list[str]:
    if not resume_context.tape_tail:
        return ["No tape tail is available for this session."]
    return [
        (
            f"Latest tape tail ({len(resume_context.tape_tail)} of "
            f"{resume_context.tape_entry_count} entries):"
        ),
        _compact_resume_json(list(resume_context.tape_tail)),
    ]


def _resume_plan_note_lines(resume_context: RuntimeResumeContext) -> list[str]:
    if resume_context.latest_plan_note is None:
        return ["No recent plan note was found in the tape tail."]
    return ["Latest plan/checkpoint note:", resume_context.latest_plan_note]


def _resume_checkpoint_line(resume_context: RuntimeResumeContext) -> str:
    if resume_context.latest_checkpoint_id is None:
        return "No checkpoint is available for this session."
    label = (
        f" ({resume_context.latest_checkpoint_label})"
        if resume_context.latest_checkpoint_label is not None
        else ""
    )
    return f"Latest checkpoint: {resume_context.latest_checkpoint_id}{label}."


def _resume_boundary_anchor_meta(
    resume_context: RuntimeResumeContext,
) -> JSONObject:
    metadata = resume_context.metadata()
    metadata["product_anchor_type"] = RESUME_BOUNDARY_PRODUCT_ANCHOR_TYPE
    metadata["skip"] = True
    metadata["included_anchor_ids"] = []
    return metadata


__all__ = [
    "DEFAULT_RESUME_PROMPT",
    "AttachedRuntimeRunRequester",
    "MessageSnapshotLoader",
    "RuntimeLiveBoundaryAnchorAppender",
    "RuntimeResumeOrchestrationService",
    "RuntimeResumeOrchestrationSession",
    "RuntimeResumeContext",
    "RuntimeResumeService",
    "RuntimeResumeSession",
    "RuntimeRunDispatcher",
    "RuntimeRunIdFactory",
    "RuntimeRunLoader",
    "RuntimeSessionAttachedPredicate",
    "RuntimeSessionPersister",
    "TapeEntryLoader",
    "TapeEntrySaver",
]
