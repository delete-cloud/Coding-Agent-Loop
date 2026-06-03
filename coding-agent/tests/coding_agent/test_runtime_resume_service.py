from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from agentkit.checkpoint.models import CheckpointMeta
from agentkit.tape.models import Entry

from coding_agent.runtime_store import AgentRunRecord, RunMessageSnapshotRecord
from coding_agent.runs import RuntimeResumeOrchestrationService, RuntimeResumeService


@dataclass
class FakeSession:
    id: str = "session-1"
    tape_id: str | None = "tape-1"
    turn_in_progress: bool = False
    turn_status: str = "idle"


@pytest.mark.asyncio
async def test_runtime_resume_service_builds_context_prompt_and_boundary_anchor() -> (
    None
):
    service = RuntimeResumeService()
    previous_run = _run("run-interrupted")

    context = await service.build_context(
        session=FakeSession(),
        previous_run=previous_run,
        resume_from_event_id="event-last",
        resume_reason="user_resume",
        list_checkpoints=_list_checkpoints,
        load_tape_entries=_load_tape_entries,
        load_message_snapshot=_load_message_snapshot,
    )
    anchor = service.resume_boundary_anchor(context)
    context = service.bind_boundary_anchor(context, anchor_id=anchor.id)
    prompt = service.resume_prompt(context, prompt="continue the implementation")

    assert context.metadata() == {
        "previous_run_id": "run-interrupted",
        "resume_from_run_id": "run-interrupted",
        "resume_reason": "user_resume",
        "resume_context_injected": True,
        "resume_context_strategy": "checkpoint+tape_tail+message_snapshot",
        "checkpoint_count": 1,
        "tape_entry_count": 3,
        "resume_tape_tail_entry_count": 3,
        "resume_plan_note_included": True,
        "resume_from_event_id": "event-last",
        "latest_checkpoint_id": "cp-latest",
        "latest_checkpoint_label": "latest",
        "latest_message_snapshot_id": "run-interrupted:latest",
        "latest_message_snapshot_message_count": 2,
        "resume_boundary_anchor_id": anchor.id,
        "resume_boundary_anchor_type": "resume_boundary",
    }
    assert anchor.anchor_type == "context"
    assert anchor.payload == {"label": "Resume boundary"}
    assert anchor.meta["product_anchor_type"] == "resume_boundary"
    assert anchor.meta["skip"] is True
    assert anchor.meta["previous_run_id"] == "run-interrupted"
    assert "Previous run was interrupted." in prompt
    assert "Latest checkpoint: cp-latest (latest)." in prompt
    assert "Latest runtime message snapshot: run-interrupted:latest (2 messages)." in (
        prompt
    )
    assert "Latest tape tail (3 of 3 entries):" in prompt
    assert "todo_write" in prompt
    assert "Wire resume context to tape tail" in prompt
    assert "Latest plan/checkpoint note:" in prompt
    assert "it does not restore or roll back to a checkpoint" in prompt
    assert "continue the implementation" in prompt
    assert "resume_boundary" not in prompt


@pytest.mark.asyncio
async def test_runtime_resume_service_reports_empty_context_without_tape() -> None:
    service = RuntimeResumeService()
    previous_run = _run("run-cancelled")

    context = await service.build_context(
        session=FakeSession(tape_id=None),
        previous_run=previous_run,
        resume_from_event_id=None,
        resume_reason="remote_resume",
        list_checkpoints=_list_checkpoints,
        load_tape_entries=_load_tape_entries,
        load_message_snapshot=_load_missing_message_snapshot,
    )
    prompt = service.resume_prompt(context, prompt=None)

    assert context.checkpoint_count == 0
    assert context.latest_checkpoint_id is None
    assert context.tape_entry_count == 0
    assert context.tape_tail == ()
    assert context.latest_plan_note is None
    assert context.latest_message_snapshot_id is None
    assert "No runtime events were recorded for the previous run." in prompt
    assert "No checkpoint is available for this session." in prompt
    assert "No tape tail is available for this session." in prompt
    assert "No recent plan note was found in the tape tail." in prompt
    assert "Continue from the last known state." in prompt


@pytest.mark.asyncio
async def test_runtime_resume_orchestration_runs_local_resume_with_boundary_anchor() -> (
    None
):
    session = FakeSession(tape_id=None)
    previous_run = _run("run-interrupted")
    completed_run = _run("run-resumed")
    persisted_sessions: list[FakeSession] = []
    saved_tape_entries: list[tuple[str, list[dict[str, object]]]] = []
    live_anchor_ids: list[str] = []
    local_runs: list[tuple[str, str, str, object]] = []

    async def latest_runtime_run(session_id: str) -> AgentRunRecord | None:
        assert session_id == "session-1"
        return previous_run

    async def latest_runtime_event_id(run: AgentRunRecord) -> str | None:
        assert run is previous_run
        return "event-last"

    async def load_runtime_run(run_id: str) -> AgentRunRecord | None:
        assert run_id == "run-resumed"
        return completed_run

    async def persist_session(persisted_session: FakeSession) -> None:
        persisted_sessions.append(persisted_session)

    async def save_tape_entries(
        tape_id: str,
        entries: list[dict[str, object]],
    ) -> None:
        saved_tape_entries.append((tape_id, entries))

    async def run_local(
        session_id: str,
        prompt: str,
        run_id: str,
        resume_context: object,
    ) -> AgentRunRecord | None:
        local_runs.append((session_id, prompt, run_id, resume_context))
        return None

    async def request_attached(
        session_id: str,
        prompt: str,
        run_id: str,
        resume_context: object,
    ) -> AgentRunRecord:
        del session_id, prompt, run_id, resume_context
        raise AssertionError("attached resume should not run")

    service = RuntimeResumeOrchestrationService(
        resume_service=RuntimeResumeService(),
        latest_runtime_run=latest_runtime_run,
        latest_runtime_event_id=latest_runtime_event_id,
        load_runtime_run=load_runtime_run,
        persist_session=persist_session,
        list_checkpoints=_list_checkpoints,
        load_tape_entries=_load_tape_entries,
        save_tape_entries=save_tape_entries,
        load_message_snapshot=_load_message_snapshot,
        run_local=run_local,
        request_attached=request_attached,
        session_is_attached=lambda session: False,
        append_live_boundary_anchor=lambda session, anchor: live_anchor_ids.append(
            anchor.id
        ),
        active_resume_blocking_statuses=frozenset({"running"}),
        run_id_factory=lambda: "run-resumed",
    )

    resumed_run = await service.resume(
        session,
        prompt="continue the implementation",
        resume_reason="user_resume",
    )

    assert resumed_run is completed_run
    assert session.tape_id == "tape-1"
    assert persisted_sessions == [session]
    assert len(saved_tape_entries) == 1
    assert saved_tape_entries[0][0] == "tape-1"
    saved_entry = saved_tape_entries[0][1][0]
    assert saved_entry["kind"] == "anchor"
    assert saved_entry["anchor_type"] == "context"
    assert saved_entry["id"] == live_anchor_ids[0]
    assert len(local_runs) == 1
    assert local_runs[0][0] == "session-1"
    assert local_runs[0][2] == "run-resumed"
    assert "Previous run was interrupted." in local_runs[0][1]
    assert "continue the implementation" in local_runs[0][1]


async def _list_checkpoints(session_id: str) -> list[CheckpointMeta]:
    assert session_id == "session-1"
    return [
        CheckpointMeta(
            checkpoint_id="cp-latest",
            tape_id="tape-1",
            session_id=session_id,
            entry_count=2,
            window_start=0,
            created_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
            label="latest",
        )
    ]


async def _load_tape_entries(tape_id: str) -> list[dict[str, object]]:
    assert tape_id == "tape-1"
    return [
        Entry(
            kind="message",
            payload={"role": "user", "content": "implement resume"},
            id="entry-user",
        ).to_dict(),
        Entry(
            kind="tool_call",
            payload={
                "tool_name": "todo_write",
                "tasks": [
                    {
                        "title": "Wire resume context to tape tail",
                        "status": "in_progress",
                    }
                ],
            },
            id="entry-plan",
        ).to_dict(),
        Entry(
            kind="message",
            payload={"role": "assistant", "content": "partial progress"},
            id="entry-assistant",
        ).to_dict(),
    ]


async def _load_message_snapshot(
    snapshot_id: str,
) -> RunMessageSnapshotRecord | None:
    assert snapshot_id == "run-interrupted:latest"
    return RunMessageSnapshotRecord(
        snapshot_id=snapshot_id,
        run_id="run-interrupted",
        messages=[
            {"role": "user", "content": "implement resume"},
            {"role": "assistant", "content": "partial progress"},
        ],
        metadata={
            "session_id": "session-1",
            "tape_id": "tape-1",
            "snapshot_kind": "latest_context",
        },
        created_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
    )


async def _load_missing_message_snapshot(
    snapshot_id: str,
) -> RunMessageSnapshotRecord | None:
    assert snapshot_id == "run-cancelled:latest"
    return None


def _run(run_id: str) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=run_id,
        session_id="session-1",
        tape_id="tape-1",
        parent_run_id=None,
        agent_id=None,
        status="interrupted",
        started_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC) - timedelta(minutes=5),
        ended_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
        metadata={},
        result={},
        error="runtime interrupted",
    )
