from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agentkit.storage.protocols import TapeInfo, TapeSearchResult
from httpx import ASGITransport, AsyncClient

from coding_agent.environment import WorkspaceProviderCapabilities
from coding_agent.bee_launch import BeeLaunchRecord
from coding_agent.bee_workspace import (
    BeeWorkspaceRunArtifacts,
    BeeWorkspaceRunNode,
    write_bee_workspace_run_artifacts,
)
from coding_agent.external_executor import ExecutorRunRecord
from coding_agent.runtime_store import (
    AgentInteractionRecord,
    AgentRunRecord,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
)
from coding_agent.scheduled_runs import (
    ProactiveSignalRecord,
    ScheduleRecord,
    ScheduleTriggerRecord,
)
from coding_agent.topic_store import (
    TopicAnchorRecord,
    TopicCostRecord,
    TopicRecallLinkRecord,
    TopicRecord,
)
from coding_agent.core.config import settings
from coding_agent.observability import prometheus_metrics_text, reset_prometheus_metrics
from coding_agent.ui import http_server
from coding_agent.ui.http_server import app, session_manager
from coding_agent.ui.session_manager import Session
from coding_agent.ui.workspace_store import WorkspaceRecord


CONSOLE_ROUTES = (
    "/console",
    "/console/sessions",
    "/console/runs",
    "/console/interactions",
    "/console/tape",
    "/console/context",
    "/console/memory",
    "/console/actions",
    "/console/observability",
    "/console/topics",
    "/console/schedules",
    "/console/bee",
    "/console/workspaces",
    "/console/release",
)

NAV_LINKS = {
    "Sessions": "/console/sessions",
    "Runs": "/console/runs",
    "HITL / Interactions": "/console/interactions",
    "Tape": "/console/tape",
    "Context": "/console/context",
    "Memory": "/console/memory",
    "Actions / Validation": "/console/actions",
    "Observability": "/console/observability",
    "Topics": "/console/topics",
    "Schedules": "/console/schedules",
    "Bee Tasks": "/console/bee",
    "Workspaces": "/console/workspaces",
    "Release / Health": "/console/release",
}

FORBIDDEN_RENDERED_TEXT = (
    "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
    "raw prompt",
    "raw message",
    "command_output",
    "stdout",
    "stderr",
    "env",
)


class _ConsoleTapeStore:
    def __init__(self, tape_ids: list[str] | None = None) -> None:
        self.tape_ids = tape_ids or ["tape-alpha"]

    async def save(self, tape_id: str, entries: list[dict[str, object]]) -> None:
        return None

    async def load(self, tape_id: str) -> list[dict[str, object]]:
        return []

    async def list_ids(self) -> list[str]:
        return list(self.tape_ids)

    async def truncate(self, tape_id: str, keep: int) -> None:
        return None

    async def info(self, tape_id: str) -> TapeInfo | None:
        if tape_id not in self.tape_ids:
            return None
        return TapeInfo(tape_id=tape_id, entry_count=3, first_seq=0, last_seq=2)

    async def search(
        self,
        *,
        tape_id: str | None = None,
        kind: str | None = None,
        run_id: str | None = None,
        tool_call_id: str | None = None,
        anchor_type: str | None = None,
        limit: int = 100,
    ) -> list[TapeSearchResult]:
        entries = []
        for known_tape_id in self.tape_ids:
            known_run_id = known_tape_id.replace("tape", "run")
            entries.extend([
                TapeSearchResult(
                    tape_id=known_tape_id,
                    seq=0,
                    entry={
                        "kind": "message",
                        "payload": {
                            "run_id": known_run_id,
                            "content": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
                        },
                    },
                ),
                TapeSearchResult(
                    tape_id=known_tape_id,
                    seq=1,
                    entry={
                        "kind": "tool_call",
                        "payload": {
                            "run_id": known_run_id,
                            "tool_call_id": "tool-alpha",
                        },
                    },
                ),
                TapeSearchResult(
                    tape_id=known_tape_id,
                    seq=2,
                    entry={
                        "kind": "anchor",
                        "meta": {"anchor_type": "handoff", "secret": "hidden"},
                    },
                ),
            ])
        filtered = []
        for result in entries:
            entry = result.entry
            payload = (
                entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
            )
            meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
            if tape_id is not None and result.tape_id != tape_id:
                continue
            if kind is not None and entry.get("kind") != kind:
                continue
            if (
                run_id is not None
                and payload.get("run_id") != run_id
                and meta.get("run_id") != run_id
            ):
                continue
            if (
                tool_call_id is not None
                and payload.get("tool_call_id") != tool_call_id
                and meta.get("tool_call_id") != tool_call_id
            ):
                continue
            if anchor_type is not None and meta.get("anchor_type") != anchor_type:
                continue
            filtered.append(result)
        if limit <= 0:
            return []
        return filtered[:limit]


class _ConsoleRuntimeStore:
    def __init__(self, runs: list[AgentRunRecord] | None = None) -> None:
        self.runs = {run.run_id: run for run in runs or []}
        self.events: dict[str, list[RuntimeEventRecord]] = {}
        self.snapshots: dict[str, RunMessageSnapshotRecord] = {}
        self.interactions: dict[str, AgentInteractionRecord] = {}

    async def create_agent_run(self, record: AgentRunRecord) -> AgentRunRecord:
        self.runs[record.run_id] = record
        return record

    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None:
        return self.runs.get(run_id)

    async def list_agent_runs(self, session_id: str) -> list[AgentRunRecord]:
        return [run for run in self.runs.values() if run.session_id == session_id]

    async def update_agent_run(
        self,
        run_id: str,
        *,
        status: str,
        ended_at: datetime | None,
        metadata: dict[str, object],
        result: dict[str, object],
        error: str | None,
    ) -> AgentRunRecord:
        current = self.runs[run_id]
        updated = AgentRunRecord(
            run_id=current.run_id,
            session_id=current.session_id,
            tape_id=current.tape_id,
            parent_run_id=current.parent_run_id,
            agent_id=current.agent_id,
            status=status,
            started_at=current.started_at,
            ended_at=ended_at,
            metadata=metadata,
            result=result,
            error=error,
        )
        self.runs[run_id] = updated
        return updated

    async def append_runtime_event(
        self,
        record: RuntimeEventRecord,
    ) -> RuntimeEventRecord:
        self.events.setdefault(record.run_id, []).append(record)
        return record

    async def load_runtime_event(self, event_id: str) -> RuntimeEventRecord | None:
        for events in self.events.values():
            for event in events:
                if event.event_id == event_id:
                    return event
        return None

    async def replay_runtime_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> list[RuntimeEventRecord]:
        events = self.events.get(run_id, [])
        return [
            event
            for event in events
            if event.sequence is not None and event.sequence > after_sequence
        ][:limit]

    async def save_message_snapshot(
        self,
        record: RunMessageSnapshotRecord,
    ) -> RunMessageSnapshotRecord:
        self.snapshots[record.snapshot_id] = record
        return record

    async def load_message_snapshot(
        self,
        snapshot_id: str,
    ) -> RunMessageSnapshotRecord | None:
        return self.snapshots.get(snapshot_id)

    async def create_agent_interaction(
        self,
        record: AgentInteractionRecord,
    ) -> AgentInteractionRecord:
        self.interactions.setdefault(record.interaction_id, record)
        return record

    async def resolve_agent_interaction(
        self,
        interaction_id: str,
        *,
        status: str,
        response_payload: dict[str, object],
        resolved_at: datetime,
    ) -> AgentInteractionRecord:
        current = self.interactions[interaction_id]
        resolved = AgentInteractionRecord(
            interaction_id=current.interaction_id,
            run_id=current.run_id,
            interaction_kind=current.interaction_kind,
            status=status,
            request_payload=current.request_payload,
            response_payload=response_payload,
            metadata=current.metadata,
            created_at=current.created_at,
            resolved_at=resolved_at,
        )
        self.interactions[interaction_id] = resolved
        return resolved

    async def list_agent_interactions(
        self,
        run_id: str,
    ) -> list[AgentInteractionRecord]:
        return [
            interaction
            for interaction in self.interactions.values()
            if interaction.run_id == run_id
        ]


class _ConsoleWorkspaceStore:
    def __init__(self, records: list[WorkspaceRecord]) -> None:
        self.records = records

    async def save(self, record: WorkspaceRecord) -> None:
        self.records.append(record)

    async def list(self) -> list[WorkspaceRecord]:
        return list(self.records)

    async def load_by_workspace_id(self, workspace_id: str) -> WorkspaceRecord | None:
        for record in self.records:
            if record.workspace_id == workspace_id:
                return record
        return None

    async def load_for_session_workspace(
        self, session_id: str, workspace_id: str
    ) -> WorkspaceRecord | None:
        for record in self.records:
            if record.session_id == session_id and record.workspace_id == workspace_id:
                return record
        return None

    async def update_status(
        self,
        workspace_record_id: str,
        *,
        status: str,
        cleanup_error: str | None = None,
    ) -> WorkspaceRecord | None:
        del workspace_record_id, status, cleanup_error
        return None

    async def update_retention(
        self,
        workspace_record_id: str,
        *,
        retention_policy: str,
        expires_at: datetime | None,
        status: str,
    ) -> WorkspaceRecord | None:
        del workspace_record_id, retention_policy, expires_at, status
        return None

    async def update_result_refs(
        self,
        workspace_record_id: str,
        *,
        result_refs: dict[str, object],
    ) -> WorkspaceRecord | None:
        del workspace_record_id, result_refs
        return None


class _ConsoleTopicStore:
    def __init__(
        self,
        topics: list[TopicRecord],
        *,
        anchors: list[TopicAnchorRecord] | None = None,
        recalls: list[TopicRecallLinkRecord] | None = None,
        costs: list[TopicCostRecord] | None = None,
    ) -> None:
        self.topics = {topic.topic_id: topic for topic in topics}
        self.anchors = anchors or []
        self.recalls = recalls or []
        self.costs = {cost.topic_id: cost for cost in costs or []}

    async def list_topics(
        self,
        *,
        session_id: str | None = None,
        tape_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[TopicRecord]:
        topics = []
        for topic in self.topics.values():
            if session_id is not None and topic.session_id != session_id:
                continue
            if tape_id is not None and topic.tape_id != tape_id:
                continue
            if status is not None and topic.status != status:
                continue
            topics.append(topic)
        return topics[:limit]

    async def load_topic(self, topic_id: str) -> TopicRecord | None:
        return self.topics.get(topic_id)

    async def list_topic_anchors(self, topic_id: str) -> list[TopicAnchorRecord]:
        return [anchor for anchor in self.anchors if anchor.topic_id == topic_id]

    async def list_recall_links(
        self,
        source_topic_id: str,
    ) -> list[TopicRecallLinkRecord]:
        return [
            recall
            for recall in self.recalls
            if recall.source_topic_id == source_topic_id
        ]

    async def load_topic_cost(self, topic_id: str) -> TopicCostRecord | None:
        return self.costs.get(topic_id)


class _ConsoleScheduledRunStore:
    def __init__(
        self,
        schedules: list[ScheduleRecord],
        *,
        triggers: list[ScheduleTriggerRecord] | None = None,
        signals: list[ProactiveSignalRecord] | None = None,
    ) -> None:
        self.schedules = schedules
        self.triggers = triggers or []
        self.signals = signals or []

    async def list_schedules(
        self,
        *,
        session_id: str | None = None,
        topic_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ScheduleRecord]:
        schedules = []
        for schedule in self.schedules:
            if session_id is not None and schedule.session_id != session_id:
                continue
            if topic_id is not None and schedule.topic_id != topic_id:
                continue
            if status is not None and schedule.status != status:
                continue
            schedules.append(schedule)
        return schedules[:limit]

    async def list_triggers(
        self,
        schedule_id: str,
        *,
        limit: int = 100,
    ) -> list[ScheduleTriggerRecord]:
        return [
            trigger for trigger in self.triggers if trigger.schedule_id == schedule_id
        ][:limit]

    async def list_signals(
        self,
        *,
        status: str | None = None,
        session_id: str | None = None,
        topic_id: str | None = None,
        limit: int = 100,
    ) -> list[ProactiveSignalRecord]:
        signals = []
        for signal in self.signals:
            if status is not None and signal.status != status:
                continue
            if session_id is not None and signal.session_id != session_id:
                continue
            if topic_id is not None and signal.topic_id != topic_id:
                continue
            signals.append(signal)
        return signals[:limit]


class _ConsoleBeeLaunchStore:
    def __init__(self, launches: list[BeeLaunchRecord]) -> None:
        self.launches = launches

    async def list_launches(
        self,
        *,
        source: str | None = None,
        status: str | None = None,
        session_id: str | None = None,
        topic_id: str | None = None,
        limit: int = 100,
    ) -> list[BeeLaunchRecord]:
        launches = []
        for launch in self.launches:
            if source is not None and launch.source != source:
                continue
            if status is not None and launch.status != status:
                continue
            if session_id is not None and launch.session_id != session_id:
                continue
            if topic_id is not None and launch.topic_id != topic_id:
                continue
            launches.append(launch)
        return launches[:limit]


class _ConsoleExecutorRunStore:
    def __init__(self, records: list[ExecutorRunRecord]) -> None:
        self.records = records

    async def list_executor_runs(
        self,
        *,
        task_id: str | None = None,
        node_id: str | None = None,
        executor_kind: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ExecutorRunRecord]:
        records = []
        for record in self.records:
            if task_id is not None and record.task_id != task_id:
                continue
            if node_id is not None and record.node_id != node_id:
                continue
            if executor_kind is not None and record.executor_kind != executor_kind:
                continue
            if status is not None and record.status != status:
                continue
            records.append(record)
        return records[:limit]


@pytest.fixture(autouse=True)
async def clear_console_state() -> AsyncIterator[None]:
    session_manager.configure_runtime_store(None)
    session_manager.configure_workspace_metadata_store(None)
    original_tape_store = session_manager._tape_store
    reset_prometheus_metrics()
    session_manager.clear_sessions()
    yield
    session_manager.configure_runtime_store(None)
    session_manager.configure_workspace_metadata_store(None)
    session_manager._tape_store = original_tape_store
    reset_prometheus_metrics()
    session_manager.clear_sessions()


def _register_console_session(
    session_id: str,
    *,
    status: str = "created",
    owner_label: str | None = None,
) -> Session:
    created_at = datetime(2026, 5, 20, 1, 2, 3, tzinfo=UTC)
    session = Session(
        id=session_id,
        created_at=created_at,
        last_activity=datetime(2026, 5, 20, 1, 3, 4, tzinfo=UTC),
        provider_name="fixture-provider",
        model_name="fixture-model",
        origin=None if owner_label is None else {"owner_label": owner_label},
    )
    if status == "running":
        session.turn_in_progress = True
        session.turn_status = "running"
        session.current_turn_id = f"{session_id}-turn"
    elif status == "failed":
        session.turn_status = "failed"
        session.last_failure_details = "hidden failure details"
    elif status == "waiting_approval":
        session.pending_approval = {"request_id": "approval-secret-payload"}
    session_manager.register_session(session)
    return session


def _runtime_run(
    run_id: str,
    session_id: str,
    *,
    status: str,
    error: str | None = None,
    tape_id: str | None = None,
) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=run_id,
        session_id=session_id,
        tape_id=tape_id or f"{session_id}-tape",
        parent_run_id=None,
        agent_id=None,
        status=status,
        started_at=datetime(2026, 5, 20, 2, 0, 0, tzinfo=UTC),
        ended_at=(
            None if status == "running" else datetime(2026, 5, 20, 2, 1, 0, tzinfo=UTC)
        ),
        metadata={
            "prompt": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
            "context_pack": {
                "sections": [
                    {
                        "title": "Repo references",
                        "items": [
                            {
                                "source_kind": "repo_file",
                                "source_id": "repo-src-auth",
                                "label": "Auth module",
                                "repo_path": "src/auth.py",
                                "line_start": 10,
                                "line_end": 20,
                                "score": 0.12,
                                "body": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
                                "evidence": [
                                    {
                                        "kind": "repo_file",
                                        "source_id": "repo-src-auth",
                                        "label": "reason: auth evidence",
                                        "repo_path": "src/auth.py",
                                        "line_start": 10,
                                        "line_end": 20,
                                        "chunk_id": "chunk-auth",
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "title": "Memory references",
                        "items": [
                            {
                                "source_kind": "memory",
                                "source_id": "mem-pack-ref",
                                "label": "Compacted memory prose that must not render",
                                "repo_path": "src/memory.py",
                                "line_start": 5,
                                "line_end": 6,
                                "evidence": [
                                    {
                                        "kind": "repo_file",
                                        "label": "memory evidence",
                                        "repo_path": "src/memory.py",
                                    }
                                ],
                            }
                        ],
                    },
                ]
            },
            "memory_evidence": [
                {
                    "source_id": "memory-auth-policy",
                    "summary": "Auth regression memory",
                    "label": "memory_auth_policy",
                    "status": "accepted",
                    "tags": ["src/auth.py", "tests/auth"],
                    "evidence": [
                        {
                            "repo_path": "src/auth.py",
                            "label": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
                        }
                    ],
                    "repo_path": "src/auth.py",
                    "line_start": 30,
                    "line_end": 32,
                }
            ],
            "actions": [
                {
                    "action_id": "action-alpha",
                    "kind": "patch",
                    "status": "completed",
                    "policy_decision": "allow",
                    "risk_level": "medium",
                    "changed_path_count": 2,
                    "file_extension_buckets": ".py,.md",
                    "approval_interaction_id": "interaction-pending",
                    "approval_status": "approved",
                    "validation_id": "validation-alpha",
                    "patch_summary": {
                        "hunk_count": 3,
                        "changed_path_count": 2,
                        "patch_content": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
                    },
                    "command": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
                }
            ],
            "retrieval_id": "retrieval-alpha",
            "validation_report": {
                "status": "failed",
                "outcomes": [
                    {
                        "label": "pytest_auth",
                        "status": "failed",
                        "exit_code": 1,
                        "duration_ms": 42,
                        "policy": {"decision": "allow"},
                        "failure_summary": {
                            "stdout_bytes": 12,
                            "stderr_bytes": 20,
                            "stdout_lines": 1,
                            "stderr_lines": 2,
                            "raw_output": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
                        },
                    }
                ],
            },
        },
        result={"content": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT"},
        error=error,
    )


def _topic_runtime_run(run_id: str = "run-alpha") -> AgentRunRecord:
    base = _runtime_run(run_id, "session-alpha", status="completed")
    metadata = dict(base.metadata)
    metadata["topic_id"] = "topic-auth"
    metadata["topic"] = {
        "topic_id": "topic-auth",
        "tape_id": "session-alpha-tape",
        "session_id": "session-alpha",
        "kind": "coding",
        "status": "finalized",
        "title": "Auth topic",
        "summary": "JWT validation moved safely",
        "topic_initial_seq": 0,
        "topic_finalized_seq": 9,
        "anchors": [
            {
                "seq": 0,
                "anchor_type": "topic_initial",
                "entry_id": "entry-topic-start",
            },
            {
                "seq": 9,
                "anchor_type": "topic_finalized",
                "entry_id": "entry-topic-end",
            },
        ],
        "recall_links": [
            {
                "recalled_topic_id": "topic-prior",
                "relation": "summary_recall",
                "anchor_seq": 4,
            }
        ],
        "cost": {
            "prompt_tokens": 10,
            "completion_tokens": 7,
            "total_tokens": 17,
            "run_count": 1,
            "action_count": 1,
            "validation_count": 1,
            "tool_call_count": 2,
        },
    }
    return AgentRunRecord(
        run_id=base.run_id,
        session_id=base.session_id,
        tape_id=base.tape_id,
        parent_run_id=base.parent_run_id,
        agent_id=base.agent_id,
        status=base.status,
        started_at=base.started_at,
        ended_at=base.ended_at,
        metadata=metadata,
        result=base.result,
        error=base.error,
    )


def _bee_runtime_run(run_id: str = "run-bee") -> AgentRunRecord:
    base = _runtime_run(run_id, "session-alpha", status="completed")
    metadata = dict(base.metadata)
    metadata.update({
        "bee_runtime": "task_launch",
        "launch_id": "launch-alpha",
        "launch_source": "schedule",
        "launch_status": "launched",
        "launch_kind": "durable_run",
        "task_id": "bee-task-alpha",
        "node_id": "node-validate",
        "topic_id": "topic-auth",
        "session_id": "session-alpha",
        "template_id": "launch-blueprint-alpha",
        "schedule_id": "schedule-alpha",
        "task_kind": "maintenance",
        "task_profile": "local",
        "node_kind": "validation",
        "node_profile": "default",
        "approval_policy": "existing_runtime_policy",
        "action_policy": "existing_action_safety",
        "workspace_binding": "existing_workspace_provider",
        "workspace_policy": "default",
        "context_profile": "repo",
        "context_reference": "profile_only",
        "validation_profile": "pytest",
        "validation_reference": "profile_only",
        "executor_run_id": "executor-run-alpha",
        "executor_kind": "local",
        "executor_status": "succeeded",
        "executor_capability": "available",
        "executor_summary": "Local executor succeeded",
        "prompt": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
        "command_output": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
    })
    return AgentRunRecord(
        run_id=base.run_id,
        session_id=base.session_id,
        tape_id=base.tape_id,
        parent_run_id=base.parent_run_id,
        agent_id=base.agent_id,
        status=base.status,
        started_at=base.started_at,
        ended_at=base.ended_at,
        metadata=metadata,
        result=base.result,
        error=base.error,
    )


def _write_console_bee_workspace_fixture(workspace_root: Path) -> None:
    template_dir = workspace_root / ".bee" / "templates" / "template-alpha"
    feature_dir = template_dir / "features"
    feature_dir.mkdir(parents=True)
    (template_dir / "metadata.yaml").write_text(
        "\n".join([
            "version: 1",
            "template_id: template-alpha",
            "kind: maintenance",
            "profile: local",
            "title: Local template",
            "topic:",
            "  session_id: session-alpha",
            "nodes:",
            "  - node_id: node-plan",
            "    kind: analysis",
            "    profile: default",
            "    title: Plan local task",
        ]),
        encoding="utf-8",
    )
    (template_dir / "SKILL.md").write_text("# Template Skill\n", encoding="utf-8")
    (feature_dir / "acceptance.feature").write_text(
        "Feature: safe console template\n", encoding="utf-8"
    )
    (template_dir / "commands.yaml").write_text(
        "\n".join([
            "commands:",
            "  - name: smoke",
            "    profile: validation",
            "    policy: existing_command_policy",
            "    category: validation",
            "    validation_label: pytest_smoke",
            "    metadata:",
            "      owner: local",
        ]),
        encoding="utf-8",
    )
    write_bee_workspace_run_artifacts(
        workspace_root,
        BeeWorkspaceRunArtifacts(
            task_id="bee-task-alpha",
            template_id="template-alpha",
            topic_id="topic-auth",
            status="completed",
            nodes=(
                BeeWorkspaceRunNode(
                    node_id="node-validate",
                    status="completed",
                    run_id="run-bee",
                    action_ids=("action-alpha",),
                    validation_ids=("validation-alpha",),
                    attempts=1,
                ),
            ),
            run_ids=("run-bee",),
            action_ids=("action-alpha",),
            validation_ids=("validation-alpha",),
            report_title="Local Bee task completed",
            report_summary="Validation passed with sanitized evidence.",
        ),
    )


def _topic_record(topic_id: str = "topic-durable") -> TopicRecord:
    return TopicRecord(
        topic_id=topic_id,
        tape_id="session-alpha-tape",
        session_id="session-alpha",
        kind="coding",
        status="finalized",
        title="Durable topic",
        summary="Durable topic summary",
        owner="local",
        topic_initial_seq=2,
        topic_finalized_seq=11,
        created_at=datetime(2026, 5, 20, 2, 0, 0, tzinfo=UTC),
        finalized_at=datetime(2026, 5, 20, 2, 1, 0, tzinfo=UTC),
        metadata={"profile": "local"},
    )


def _schedule_record(schedule_id: str = "schedule-alpha") -> ScheduleRecord:
    return ScheduleRecord(
        schedule_id=schedule_id,
        session_id="session-alpha",
        topic_id="topic-auth",
        kind="interval",
        status="active",
        cadence="daily",
        owner="owner:fixture",
        title="Daily topic check",
        next_due_at=datetime(2026, 5, 21, 2, 0, 0, tzinfo=UTC),
        last_triggered_at=datetime(2026, 5, 20, 2, 0, 0, tzinfo=UTC),
        created_at=datetime(2026, 5, 19, 2, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 5, 20, 2, 0, 0, tzinfo=UTC),
        metadata={"profile": "local"},
    )


def _schedule_trigger_record(
    trigger_id: str = "trigger-alpha",
    *,
    schedule_id: str = "schedule-alpha",
) -> ScheduleTriggerRecord:
    return ScheduleTriggerRecord(
        trigger_id=trigger_id,
        schedule_id=schedule_id,
        signal_id="signal-alpha",
        topic_id="topic-auth",
        run_id="run-alpha",
        status="planned",
        due_at=datetime(2026, 5, 21, 2, 0, 0, tzinfo=UTC),
        planned_at=datetime(2026, 5, 21, 2, 0, 1, tzinfo=UTC),
        reason="proactive_signal",
        metadata={"trigger_kind": "proactive_signal"},
    )


def _proactive_signal_record(signal_id: str = "signal-alpha") -> ProactiveSignalRecord:
    return ProactiveSignalRecord(
        signal_id=signal_id,
        dedupe_key="repo_activity:auth",
        session_id="session-alpha",
        topic_id="topic-auth",
        kind="repo_activity",
        status="planned",
        observed_at=datetime(2026, 5, 21, 1, 55, 0, tzinfo=UTC),
        cooldown_until=datetime(2026, 5, 21, 2, 25, 0, tzinfo=UTC),
        summary="Repository activity detected",
        metadata={"source_kind": "repo_activity"},
    )


def _workspace_record(
    workspace_id: str,
    *,
    status: str = "active",
    provider_instance_id: str = "docker-local",
    cleanup_error: str | None = None,
) -> WorkspaceRecord:
    return WorkspaceRecord(
        workspace_record_id=f"record-{workspace_id}",
        workspace_id=workspace_id,
        session_id="session-alpha",
        provider="docker",
        provider_instance_id=provider_instance_id,
        workspace_root_ref="/workspaces",
        workspace_host_label="local-host",
        owner_label="owner:fixture",
        source_kind="git",
        source_ref={"remote_url": "https://example.test/repo.git"},
        status=status,
        retention_policy="ttl",
        expires_at=datetime(2026, 5, 21, 2, 0, 0, tzinfo=UTC),
        cleanup_error=cleanup_error,
        result_refs={
            "branch_url": "https://example.test/branch",
            "secret_token": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
        },
        created_at=datetime(2026, 5, 20, 2, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 5, 20, 2, 5, 0, tzinfo=UTC),
    )


def _owner_label(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"owner:{digest}"


def _write_console_auth_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "server.toml"
    config_path.write_text(
        """
[agent]
name = "test-agent"
model = "test-model"
provider = "openai"

[server]
bearer_token = "user-token-a"
admin_bearer_token = "admin-token"
""".strip(),
        encoding="utf-8",
    )
    return config_path


def _runtime_event(
    event_id: str,
    run_id: str,
    *,
    event_kind: str,
    sequence: int,
) -> RuntimeEventRecord:
    return RuntimeEventRecord(
        event_id=event_id,
        run_id=run_id,
        event_kind=event_kind,
        payload={
            "message_type": event_kind,
            "content": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
            "tool_call_id": "tool-secret",
        },
        created_at=datetime(2026, 5, 20, 2, 0, sequence, tzinfo=UTC),
        sequence=sequence,
    )


def _interaction(
    interaction_id: str,
    run_id: str,
    *,
    status: str,
    resolved: bool = False,
) -> AgentInteractionRecord:
    created_at = datetime(2026, 5, 20, 2, 2, 0, tzinfo=UTC)
    return AgentInteractionRecord(
        interaction_id=interaction_id,
        run_id=run_id,
        interaction_kind="approval",
        status=status,
        request_payload={
            "tool_call": {"id": "tool-secret"},
            "prompt": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
        },
        response_payload={"content": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT"},
        metadata={
            "session_id": "session-alpha",
            "tool_call_id": "tool-call-visible",
            "tool_name": "bash_run",
            "secret": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
        },
        created_at=created_at,
        resolved_at=(datetime(2026, 5, 20, 2, 3, 0, tzinfo=UTC) if resolved else None),
    )


async def _configure_run_detail_fixture(
    *,
    status: str = "completed",
    error: str | None = None,
) -> _ConsoleRuntimeStore:
    _register_console_session("session-detail")
    store = _ConsoleRuntimeStore([
        _runtime_run(
            "run-detail",
            "session-detail",
            status=status,
            error=error,
        )
    ])
    await store.save_message_snapshot(
        RunMessageSnapshotRecord(
            snapshot_id="run-detail:latest",
            run_id="run-detail",
            messages=[
                {
                    "role": "user",
                    "content": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
                },
                {"role": "assistant", "tool_calls": [{"id": "tool-secret"}]},
            ],
            metadata={
                "snapshot_kind": "latest_context",
                "prompt": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
            },
            created_at=datetime(2026, 5, 20, 2, 0, 10, tzinfo=UTC),
        )
    )
    await store.append_runtime_event(
        _runtime_event(
            "event-2",
            "run-detail",
            event_kind="wire.TurnEnd",
            sequence=2,
        )
    )
    await store.append_runtime_event(
        _runtime_event(
            "event-1",
            "run-detail",
            event_kind="wire.StreamDelta",
            sequence=1,
        )
    )
    session_manager.configure_runtime_store(store)
    return store


async def _configure_console_e2e_fixture() -> None:
    _register_console_session("session-alpha")
    session_manager._tape_store = _ConsoleTapeStore(["session-alpha-tape"])
    store = _ConsoleRuntimeStore([_topic_runtime_run(), _bee_runtime_run()])
    await store.save_message_snapshot(
        RunMessageSnapshotRecord(
            snapshot_id="run-alpha:latest",
            run_id="run-alpha",
            messages=[
                {
                    "role": "user",
                    "content": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
                },
                {
                    "role": "assistant",
                    "content": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
                },
            ],
            metadata={"snapshot_kind": "latest_context"},
            created_at=datetime(2026, 5, 20, 2, 0, 10, tzinfo=UTC),
        )
    )
    await store.append_runtime_event(
        _runtime_event(
            "event-alpha-1",
            "run-alpha",
            event_kind="wire.StreamDelta",
            sequence=1,
        )
    )
    await store.append_runtime_event(
        _runtime_event(
            "event-alpha-2",
            "run-alpha",
            event_kind="wire.TurnEnd",
            sequence=2,
        )
    )
    await store.create_agent_interaction(
        _interaction("interaction-pending", "run-alpha", status="pending")
    )
    await store.create_agent_interaction(
        _interaction(
            "interaction-approved", "run-alpha", status="approved", resolved=True
        )
    )
    session_manager.configure_runtime_store(store)


@pytest.mark.asyncio
async def test_console_shell_routes_render_navigation_without_secrets() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for route in CONSOLE_ROUTES:
            response = await client.get(
                route,
                params={"secret": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT"},
            )

            assert response.status_code == 200, route
            assert response.headers["content-type"].startswith("text/html")
            assert "<!doctype html>" in response.text.casefold()
            assert "Developer Console" in response.text
            for label, href in NAV_LINKS.items():
                assert label in response.text
                assert f'href="{href}"' in response.text
            for forbidden in FORBIDDEN_RENDERED_TEXT:
                assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_placeholder_pages_render_empty_states() -> None:
    expected = {
        "/console": "Console Overview",
        "/console/sessions": "Sessions",
        "/console/runs": "Runs",
        "/console/interactions": "HITL / Interactions",
        "/console/tape": "Tape",
        "/console/context": "Context",
        "/console/memory": "Memory",
        "/console/actions": "Actions / Validation",
        "/console/topics": "Topics",
        "/console/schedules": "Schedules",
        "/console/bee": "Bee Tasks",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for route, title in expected.items():
            response = await client.get(route)

            assert response.status_code == 200, route
            assert f"<h1>{title}</h1>" in response.text
            assert "No data loaded yet." in response.text


@pytest.mark.asyncio
async def test_developer_console_e2e_smoke_covers_debug_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _configure_console_e2e_fixture()
    monkeypatch.setattr(
        http_server,
        "_load_observability_config",
        lambda: {
            "enabled": True,
            "tracing": {
                "enabled": True,
                "backend": "langfuse",
                "public_url": "https://langfuse.example.test/project/demo",
            },
            "metrics": {
                "enabled": True,
                "endpoint_enabled": True,
                "backend": "prometheus",
                "grafana_url": "http://localhost:3000/d/coding-agent-observability",
            },
        },
    )
    pages = {
        "/console": ("Console Overview",),
        "/console/sessions": ("session-alpha",),
        "/console/runs": ("run-alpha", "completed"),
        "/console/runs/run-alpha": (
            "Run Metadata",
            "Runtime Events",
            "Message Snapshot",
        ),
        "/console/interactions": ("interaction-pending", "interaction-approved"),
        "/console/tape?tape_id=session-alpha-tape": ("Tape Info", "Tape Search"),
        "/console/context?run_id=run-alpha": ("Context Inspector", "Auth module"),
        "/console/memory?run_id=run-alpha": ("Memory Evidence", "memory-auth-policy"),
        "/console/actions?run_id=run-alpha": (
            "Action Executions",
            "Validation Results",
            "action-alpha",
        ),
        "/console/observability?run_id=run-alpha": (
            "Trace Correlation",
            "topic-auth",
            "retrieval-alpha",
            "Langfuse",
            "Grafana",
        ),
        "/console/topics": ("Topic List", "topic-auth", "finalized"),
        "/console/topics/topic-auth": (
            "Topic Range Summary",
            "Topic Anchors",
            "Recall Links",
            "Topic Cost",
            "summary_recall",
            "topic-prior",
            "action-alpha",
            "pytest_auth",
        ),
        "/console/schedules": ("Schedules",),
        "/console/bee": (
            "Bee Task List",
            "Bee Node Launches",
            "bee-task-alpha",
            "node-validate",
            "existing_action_safety",
        ),
        "/console/release": ("Health / Readiness", "durable-runtime-smoke"),
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path, expected_text in pages.items():
            response = await client.get(path)

            assert response.status_code == 200, path
            for text in expected_text:
                assert text in response.text, path
            for label, href in NAV_LINKS.items():
                assert label in response.text
                assert f'href="{href}"' in response.text
            for forbidden in FORBIDDEN_RENDERED_TEXT:
                assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_observability_renders_configured_links_and_correlation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_console_session("session-alpha")
    session_manager.configure_runtime_store(
        _ConsoleRuntimeStore([
            _runtime_run("run-alpha", "session-alpha", status="completed")
        ])
    )
    monkeypatch.setattr(
        http_server,
        "_load_observability_config",
        lambda: {
            "enabled": True,
            "tracing": {
                "enabled": True,
                "backend": "langfuse",
                "public_url": "https://langfuse.example.test/project/demo",
                "public_key": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
            },
            "metrics": {
                "enabled": True,
                "endpoint_enabled": True,
                "backend": "prometheus",
                "grafana_url": "http://localhost:3000/d/coding-agent-observability",
                "token": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
            },
        },
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/console/observability",
            params={"run_id": "run-alpha"},
        )

    assert response.status_code == 200
    assert "Trace Correlation" in response.text
    assert "session-alpha" in response.text
    assert "run-alpha" in response.text
    assert "retrieval-alpha" in response.text
    assert "action-alpha" in response.text
    assert "validation-alpha" in response.text
    assert "interaction-pending" in response.text
    assert "langfuse" in response.text
    assert "prometheus" in response.text
    assert "https://langfuse.example.test/project/demo" in response.text
    assert "http://localhost:3000/d/coding-agent-observability" in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_observability_degrades_without_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        http_server,
        "_load_observability_config",
        lambda: {
            "enabled": True,
            "tracing": {"enabled": True, "backend": "otlp_http"},
            "metrics": {"enabled": False, "endpoint_enabled": False},
            "grafana_url": "https://grafana.example.test/?token=SECRET",
        },
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/observability")

    assert response.status_code == 200
    assert "Trace Correlation" in response.text
    assert "not configured" in response.text
    assert "otlp_http" in response.text
    assert "disabled at" in response.text
    assert "grafana.example.test" not in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_topics_render_list_detail_and_safe_provenance() -> None:
    await _configure_console_e2e_fixture()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        list_response = await client.get("/console/topics")
        detail_response = await client.get("/console/topics/topic-auth")

    assert list_response.status_code == 200
    assert "Topic List" in list_response.text
    assert "topic-auth" in list_response.text
    assert "finalized" in list_response.text
    assert "0-9" in list_response.text
    assert detail_response.status_code == 200
    assert "Topic Range Summary" in detail_response.text
    assert "Topic Anchors" in detail_response.text
    assert "topic_initial" in detail_response.text
    assert "topic_finalized" in detail_response.text
    assert "Recall Links" in detail_response.text
    assert "topic-prior" in detail_response.text
    assert "Topic Cost" in detail_response.text
    assert "Total Tokens" in detail_response.text
    assert "action-alpha" in detail_response.text
    assert "pytest_auth" in detail_response.text
    assert 'href="/console/context?run_id=run-alpha"' in detail_response.text
    for rendered in (list_response.text, detail_response.text):
        for forbidden in FORBIDDEN_RENDERED_TEXT:
            assert forbidden not in rendered


@pytest.mark.asyncio
async def test_console_topics_prefer_durable_topic_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_console_session("session-alpha")
    topic = _topic_record()
    monkeypatch.setattr(
        http_server,
        "_console_topic_store",
        lambda: _ConsoleTopicStore(
            [topic],
            anchors=[
                TopicAnchorRecord(
                    topic_id=topic.topic_id,
                    tape_id=topic.tape_id,
                    seq=2,
                    anchor_type="topic_initial",
                    entry_id="entry-durable-start",
                )
            ],
            recalls=[
                TopicRecallLinkRecord(
                    source_topic_id=topic.topic_id,
                    recalled_topic_id="topic-older",
                    relation="summary_recall",
                    anchor_seq=6,
                )
            ],
            costs=[
                TopicCostRecord(
                    topic_id=topic.topic_id,
                    prompt_tokens=20,
                    completion_tokens=8,
                    total_tokens=28,
                    run_count=3,
                    action_count=2,
                    validation_count=1,
                    tool_call_count=4,
                )
            ],
        ),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        list_response = await client.get("/console/topics")
        detail_response = await client.get("/console/topics/topic-durable")

    assert list_response.status_code == 200
    assert "topic-durable" in list_response.text
    assert "Durable topic" in list_response.text
    assert "28" in list_response.text
    assert detail_response.status_code == 200
    assert "entry-durable-start" in detail_response.text
    assert "topic-older" in detail_response.text
    assert "Prompt Tokens" in detail_response.text
    assert "20" in detail_response.text


@pytest.mark.asyncio
async def test_console_topics_do_not_emit_topic_ids_as_metric_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _configure_console_e2e_fixture()
    monkeypatch.setattr(
        http_server,
        "_load_observability_config",
        lambda: {
            "enabled": True,
            "metrics": {
                "enabled": True,
                "endpoint_enabled": True,
                "backend": "prometheus",
            },
        },
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/topics/topic-auth")

    assert response.status_code == 200
    metrics = prometheus_metrics_text()
    assert 'route="console_topics_detail"' in metrics
    assert "topic-auth" not in metrics
    assert "topic_id" not in metrics


@pytest.mark.asyncio
async def test_console_schedules_render_schedules_triggers_and_signals_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_console_session("session-alpha")
    monkeypatch.setattr(
        http_server,
        "_console_scheduled_run_store",
        lambda: _ConsoleScheduledRunStore(
            [_schedule_record()],
            triggers=[
                _schedule_trigger_record(),
                _schedule_trigger_record(
                    "signal-trigger-alpha",
                    schedule_id="signal:signal-alpha",
                ),
            ],
            signals=[_proactive_signal_record()],
        ),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/schedules")

    assert response.status_code == 200
    assert "Scheduled Runs" in response.text
    assert "Schedule Triggers" in response.text
    assert "Proactive Signals" in response.text
    assert "schedule-alpha" in response.text
    assert "trigger-alpha" in response.text
    assert "signal-trigger-alpha" in response.text
    assert "signal-alpha" in response.text
    assert "Repository activity detected" in response.text
    assert 'href="/console/runs/run-alpha"' in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_schedules_do_not_emit_schedule_or_signal_ids_as_metric_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_console_session("session-alpha")
    monkeypatch.setattr(
        http_server,
        "_load_observability_config",
        lambda: {
            "enabled": True,
            "metrics": {
                "enabled": True,
                "endpoint_enabled": True,
                "backend": "prometheus",
            },
        },
    )
    monkeypatch.setattr(
        http_server,
        "_console_scheduled_run_store",
        lambda: _ConsoleScheduledRunStore(
            [_schedule_record()],
            triggers=[_schedule_trigger_record()],
            signals=[_proactive_signal_record()],
        ),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/schedules")

    assert response.status_code == 200
    metrics = prometheus_metrics_text()
    assert 'route="console_schedules"' in metrics
    assert "schedule-alpha" not in metrics
    assert "signal-alpha" not in metrics
    assert "schedule_id" not in metrics
    assert "signal_id" not in metrics


@pytest.mark.asyncio
async def test_console_bee_renders_safe_task_and_node_summaries() -> None:
    _register_console_session("session-alpha")
    session_manager.configure_runtime_store(_ConsoleRuntimeStore([_bee_runtime_run()]))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/bee")

    assert response.status_code == 200
    assert "Bee Task List" in response.text
    assert "Bee Node Launches" in response.text
    assert "bee-task-alpha" in response.text
    assert "node-validate" in response.text
    assert "topic-auth" in response.text
    assert "maintenance" in response.text
    assert "validation" in response.text
    assert "existing_runtime_policy" in response.text
    assert "existing_action_safety" in response.text
    assert "existing_workspace_provider" in response.text
    assert 'href="/console/runs/run-bee"' in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_bee_launch_renders_safe_launch_summary() -> None:
    _register_console_session("session-alpha")
    session_manager.configure_runtime_store(_ConsoleRuntimeStore([_bee_runtime_run()]))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/bee")

    assert response.status_code == 200
    assert "Bee Launches" in response.text
    assert "launch-alpha" in response.text
    assert "schedule" in response.text
    assert "launched" in response.text
    assert "launch-blueprint-alpha" in response.text
    assert "bee-task-alpha" in response.text
    assert "topic-auth" in response.text
    assert "schedule-alpha" in response.text
    assert "Executor Runs" in response.text
    assert "executor-run-alpha" in response.text
    assert "local" in response.text
    assert "available" in response.text
    assert "Local executor succeeded" in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_bee_renders_durable_executor_run_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_console_session("session-alpha")
    session_manager.configure_runtime_store(_ConsoleRuntimeStore([]))
    monkeypatch.setattr(
        http_server,
        "_console_executor_run_store",
        lambda: _ConsoleExecutorRunStore([
            ExecutorRunRecord(
                executor_run_id="executor-run-store",
                executor_kind="kubernetes_job",
                task_id="bee-task-store",
                node_id="node-validate",
                launch_id="launch-store",
                topic_id="topic-store",
                status="succeeded",
                requested_at=datetime(2026, 5, 20, 1, 0, tzinfo=UTC),
                sanitized_summary="Kubernetes Job succeeded",
                metadata={"capability_status": "available"},
            )
        ]),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/bee")

    assert response.status_code == 200
    assert "Executor Runs" in response.text
    assert "executor-run-store" in response.text
    assert "kubernetes_job" in response.text
    assert "bee-task-store" in response.text
    assert "node-validate" in response.text
    assert "launch-store" in response.text
    assert "topic-store" in response.text
    assert "Kubernetes Job succeeded" in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_bee_launch_renders_durable_launch_store_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_console_session("session-alpha")
    session_manager.configure_runtime_store(_ConsoleRuntimeStore([]))
    monkeypatch.setattr(
        http_server,
        "_console_bee_launch_store",
        lambda: _ConsoleBeeLaunchStore([
            BeeLaunchRecord(
                launch_id="launch-manual",
                source="manual",
                template_id="template-manual",
                status="launched",
                requested_at=datetime(2026, 5, 20, 1, 0, tzinfo=UTC),
                task_id="bee-task-manual",
                topic_id="topic-manual",
                session_id="session-alpha",
                launched_at=datetime(2026, 5, 20, 1, 1, tzinfo=UTC),
            ),
            BeeLaunchRecord(
                launch_id="launch-scheduled",
                source="schedule",
                template_id="template-scheduled",
                status="launched",
                requested_at=datetime(2026, 5, 20, 2, 0, tzinfo=UTC),
                task_id="bee-task-scheduled",
                topic_id="topic-scheduled",
                session_id="session-alpha",
                schedule_id="schedule-alpha",
                launched_at=datetime(2026, 5, 20, 2, 1, tzinfo=UTC),
            ),
            BeeLaunchRecord(
                launch_id="launch-signal",
                source="proactive_signal",
                template_id="template-signal",
                status="failed",
                requested_at=datetime(2026, 5, 20, 3, 0, tzinfo=UTC),
                topic_id="topic-signal",
                session_id="session-alpha",
                signal_id="signal-alpha",
                error_type="policy_denied",
                error_message="safe policy denied",
            ),
        ]),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/bee")

    assert response.status_code == 200
    assert "Bee Launches" in response.text
    assert "launch-manual" in response.text
    assert "launch-scheduled" in response.text
    assert "launch-signal" in response.text
    assert "manual" in response.text
    assert "schedule" in response.text
    assert "proactive_signal" in response.text
    assert "schedule-alpha" in response.text
    assert "signal-alpha" in response.text
    assert "safe policy denied" in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_bee_renders_workspace_template_artifacts_and_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _register_console_session("session-alpha")
    session_manager.configure_runtime_store(_ConsoleRuntimeStore([_bee_runtime_run()]))
    _write_console_bee_workspace_fixture(tmp_path)
    monkeypatch.setattr(
        http_server,
        "_load_bee_workspace_config",
        lambda: {"workspace_root": str(tmp_path)},
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/bee")

    assert response.status_code == 200
    assert "Bee Workspace Templates" in response.text
    assert "Bee Workspace Run Artifacts" in response.text
    assert "Bee Workspace Command Intents" in response.text
    assert "template-alpha" in response.text
    assert "bee-task-alpha" in response.text
    assert "topic-auth" in response.text
    assert "pytest_smoke" in response.text
    assert "existing_command_policy" in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_bee_does_not_emit_task_or_node_ids_as_metric_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_console_session("session-alpha")
    session_manager.configure_runtime_store(_ConsoleRuntimeStore([_bee_runtime_run()]))
    monkeypatch.setattr(
        http_server,
        "_load_observability_config",
        lambda: {
            "enabled": True,
            "metrics": {
                "enabled": True,
                "endpoint_enabled": True,
                "backend": "prometheus",
            },
        },
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/bee")

    assert response.status_code == 200
    metrics = prometheus_metrics_text()
    assert 'route="console_bee"' in metrics
    assert "launch-alpha" not in metrics
    assert "bee-task-alpha" not in metrics
    assert "node-validate" not in metrics
    assert "launch_id" not in metrics
    assert "task_id" not in metrics
    assert "node_id" not in metrics


@pytest.mark.asyncio
async def test_console_bee_does_not_emit_workspace_ids_as_metric_labels(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _register_console_session("session-alpha")
    session_manager.configure_runtime_store(_ConsoleRuntimeStore([_bee_runtime_run()]))
    _write_console_bee_workspace_fixture(tmp_path)
    monkeypatch.setattr(
        http_server,
        "_load_bee_workspace_config",
        lambda: {"workspace_root": str(tmp_path)},
    )
    monkeypatch.setattr(
        http_server,
        "_load_observability_config",
        lambda: {
            "enabled": True,
            "metrics": {
                "enabled": True,
                "endpoint_enabled": True,
                "backend": "prometheus",
            },
        },
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/bee")

    assert response.status_code == 200
    metrics = prometheus_metrics_text()
    assert 'route="console_bee"' in metrics
    assert "template-alpha" not in metrics
    assert "bee-task-alpha" not in metrics
    assert "template_id" not in metrics
    assert "task_id" not in metrics


@pytest.mark.asyncio
async def test_console_bee_workspace_artifacts_require_admin_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "CODING_AGENT_SERVER_CONFIG",
        str(_write_console_auth_config(tmp_path)),
    )
    monkeypatch.setattr(settings, "http_api_key", None)
    _register_console_session(
        "session-alpha",
        owner_label=_owner_label("user-token-a"),
    )
    session_manager.configure_runtime_store(_ConsoleRuntimeStore([_bee_runtime_run()]))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _write_console_bee_workspace_fixture(workspace_root)
    monkeypatch.setattr(
        http_server,
        "_load_bee_workspace_config",
        lambda: {"workspace_root": str(workspace_root)},
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        user_response = await client.get(
            "/console/bee",
            headers={"Authorization": "Bearer user-token-a"},
        )
        admin_response = await client.get(
            "/console/bee",
            headers={"Authorization": "Bearer admin-token"},
        )

    assert user_response.status_code == 200
    assert "Bee Task List" in user_response.text
    assert "Bee Workspace Templates" in user_response.text
    assert "template-alpha" not in user_response.text
    assert "pytest_smoke" not in user_response.text
    assert admin_response.status_code == 200
    assert "template-alpha" in admin_response.text
    assert "pytest_smoke" in admin_response.text


@pytest.mark.asyncio
async def test_console_workspaces_renders_provider_inventory_without_raw_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_manager.configure_workspace_metadata_store(
        _ConsoleWorkspaceStore([
            _workspace_record("workspace-alpha"),
            _workspace_record(
                "workspace-remote",
                status="retained",
                provider_instance_id="docker-remote",
                cleanup_error="safe cleanup failure",
            ),
        ])
    )
    monkeypatch.setattr(
        http_server,
        "_load_remote_retention_config",
        lambda: {"enabled": True},
    )
    monkeypatch.setattr(
        http_server,
        "_load_cloud_workspace_config",
        lambda: {"provider": "docker", "provider_instance_id": "docker-local"},
    )
    monkeypatch.setattr(
        http_server,
        "workspace_provider_capabilities_from_config",
        lambda config: WorkspaceProviderCapabilities(
            provider=str(config["provider"]),
            available=True,
            reason="ready",
            supports_provision=True,
            supports_archive=True,
            supports_diff=True,
            supports_patch=True,
            supports_publish=False,
        ),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/workspaces")

    assert response.status_code == 200
    assert "Workspace Provider" in response.text
    assert "Workspace Inventory" in response.text
    assert "docker" in response.text
    assert "ready" in response.text
    assert "provision, archive, diff, patch" in response.text
    assert "workspace-alpha" in response.text
    assert "workspace-remote" in response.text
    assert "docker-local" in response.text
    assert "docker-remote" in response.text
    assert "local-host" in response.text
    assert "ttl" in response.text
    assert "branch_url" in response.text
    assert "secret_token" not in response.text
    assert "safe cleanup failure" in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_workspaces_does_not_emit_workspace_ids_as_metric_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_manager.configure_workspace_metadata_store(
        _ConsoleWorkspaceStore([_workspace_record("workspace-high-cardinality")])
    )
    monkeypatch.setattr(
        http_server,
        "_load_remote_retention_config",
        lambda: {"enabled": True},
    )
    monkeypatch.setattr(
        http_server,
        "_load_cloud_workspace_config",
        lambda: {"provider": "docker", "provider_instance_id": "docker-local"},
    )
    monkeypatch.setattr(
        http_server,
        "_load_observability_config",
        lambda: {
            "enabled": True,
            "metrics": {
                "enabled": True,
                "endpoint_enabled": True,
                "backend": "prometheus",
            },
        },
    )
    monkeypatch.setattr(
        http_server,
        "workspace_provider_capabilities_from_config",
        lambda config: WorkspaceProviderCapabilities(
            provider=str(config["provider"]),
            available=False,
            reason="docker_unavailable",
            supports_provision=False,
            supports_archive=False,
            supports_diff=False,
            supports_patch=False,
            supports_publish=False,
        ),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/workspaces")

    assert response.status_code == 200
    assert "workspace-high-cardinality" in response.text
    metrics = prometheus_metrics_text()
    assert 'route="console_workspaces"' in metrics
    assert "workspace-high-cardinality" not in metrics
    assert "workspace_id" not in metrics


@pytest.mark.asyncio
async def test_console_release_renders_health_and_release_manifest() -> None:
    _register_console_session("session-alpha")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/release")

    assert response.status_code == 200
    assert "Health / Readiness" in response.text
    assert "healthy" in response.text
    assert "session_store=ok" in response.text
    assert "rate_limiter=ok" in response.text
    assert "release-hardening-g38-g45" in response.text
    assert "durable-runtime-smoke" in response.text
    assert (
        "uv run pytest tests/integration/test_durable_runtime_smoke.py -v"
        in response.text
    )
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_sessions_list_renders_fixture_data_without_raw_content() -> None:
    _register_console_session("session-alpha")
    _register_console_session("session-running", status="running")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/sessions")

    assert response.status_code == 200
    assert "session-alpha" in response.text
    assert "session-running" in response.text
    assert "created" in response.text
    assert "running" in response.text
    assert "2026-05-20T01:02:03+00:00" in response.text
    assert "approval-secret-payload" not in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_tape_renders_info_and_search_without_raw_payload() -> None:
    session_manager._tape_store = _ConsoleTapeStore()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/console/tape",
            params={"tape_id": "tape-alpha", "run_id": "run-alpha"},
        )

    assert response.status_code == 200
    assert "Tape Info" in response.text
    assert "tape-alpha" in response.text
    assert "3" in response.text
    assert "Tape Search" in response.text
    assert "tool_call" in response.text
    assert "tool-alpha" in response.text
    assert "message" in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_tape_store_fixture_honors_search_limit() -> None:
    store = _ConsoleTapeStore()

    results = await store.search(tape_id="tape-alpha", limit=1)

    assert len(results) == 1
    assert results[0].seq == 0


@pytest.mark.asyncio
async def test_console_tape_restricts_user_token_to_visible_tapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CODING_AGENT_SERVER_CONFIG",
        str(_write_console_auth_config(tmp_path)),
    )
    monkeypatch.setattr(settings, "http_api_key", None)
    _register_console_session(
        "session-user",
        owner_label=_owner_label("user-token-a"),
    )
    _register_console_session(
        "session-admin",
        owner_label=_owner_label("admin-token"),
    )
    session_manager.configure_runtime_store(
        _ConsoleRuntimeStore([
            _runtime_run(
                "run-user",
                "session-user",
                status="completed",
                tape_id="tape-user",
            ),
            _runtime_run(
                "run-admin",
                "session-admin",
                status="completed",
                tape_id="tape-admin",
            ),
        ])
    )
    session_manager._tape_store = _ConsoleTapeStore(["tape-user", "tape-admin"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        forbidden = await client.get(
            "/console/tape",
            params={"tape_id": "tape-admin"},
            headers={"Authorization": "Bearer user-token-a"},
        )
        allowed = await client.get(
            "/console/tape",
            params={"tape_id": "tape-user"},
            headers={"Authorization": "Bearer user-token-a"},
        )
        admin = await client.get(
            "/console/tape",
            params={"tape_id": "tape-admin"},
            headers={"Authorization": "Bearer admin-token"},
        )

    assert forbidden.status_code == 200
    assert "tape-admin" not in forbidden.text
    assert "No tape info is available." in forbidden.text
    assert allowed.status_code == 200
    assert "tape-user" in allowed.text
    assert admin.status_code == 200
    assert "tape-admin" in admin.text


@pytest.mark.asyncio
async def test_console_tape_renders_missing_state() -> None:
    session_manager._tape_store = _ConsoleTapeStore()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/tape", params={"tape_id": "missing"})

    assert response.status_code == 200
    assert "Tape Info" in response.text
    assert "No tape info is available." in response.text


@pytest.mark.asyncio
async def test_console_context_renders_context_pack_evidence_without_body() -> None:
    _register_console_session("session-alpha")
    session_manager.configure_runtime_store(
        _ConsoleRuntimeStore([
            _runtime_run("run-alpha", "session-alpha", status="completed")
        ])
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/context", params={"run_id": "run-alpha"})

    assert response.status_code == 200
    assert "Context Inspector" in response.text
    assert "Repo references" in response.text
    assert "Auth module" in response.text
    assert "repo_file" in response.text
    assert "src/auth.py" in response.text
    assert "10-20" in response.text
    assert "0.12" in response.text
    assert "reason: auth evidence" in response.text
    assert 'href="/console/runs/run-alpha"' in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_context_renders_empty_state_for_missing_pack() -> None:
    _register_console_session("session-alpha")
    run = _runtime_run("run-alpha", "session-alpha", status="completed")
    session_manager.configure_runtime_store(
        _ConsoleRuntimeStore([
            AgentRunRecord(
                run_id=run.run_id,
                session_id=run.session_id,
                tape_id=run.tape_id,
                parent_run_id=run.parent_run_id,
                agent_id=run.agent_id,
                status=run.status,
                started_at=run.started_at,
                ended_at=run.ended_at,
                metadata={},
                result=run.result,
                error=run.error,
            )
        ])
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/context", params={"run_id": "run-alpha"})

    assert response.status_code == 200
    assert "Context Inspector" in response.text
    assert "No context pack evidence is available." in response.text


@pytest.mark.asyncio
async def test_console_memory_renders_memory_evidence_without_raw_content() -> None:
    _register_console_session("session-alpha")
    session_manager.configure_runtime_store(
        _ConsoleRuntimeStore([
            _runtime_run("run-alpha", "session-alpha", status="completed")
        ])
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/memory", params={"run_id": "run-alpha"})

    assert response.status_code == 200
    assert "Memory Evidence" in response.text
    assert "memory-auth-policy" in response.text
    assert "mem-pack-ref" in response.text
    assert "memory_auth_policy" in response.text
    assert "Compacted memory prose that must not render" not in response.text
    assert "Auth regression memory" not in response.text
    assert "accepted" in response.text
    assert "src/auth.py" in response.text
    assert "30-32" in response.text
    assert 'href="/console/runs/run-alpha"' in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_memory_renders_empty_state_for_missing_run() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/memory", params={"run_id": "missing"})

    assert response.status_code == 200
    assert "Memory Evidence" in response.text
    assert "No memory evidence is available." in response.text


@pytest.mark.asyncio
async def test_console_actions_renders_action_validation_and_policy_summaries() -> None:
    _register_console_session("session-alpha")
    session_manager.configure_runtime_store(
        _ConsoleRuntimeStore([
            _runtime_run("run-alpha", "session-alpha", status="completed")
        ])
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/actions", params={"run_id": "run-alpha"})

    assert response.status_code == 200
    assert "Action Executions" in response.text
    assert "action-alpha" in response.text
    assert "patch" in response.text
    assert "allow" in response.text
    assert "medium" in response.text
    assert ".py, .md" in response.text
    assert "interaction-pending" in response.text
    assert "validation-alpha" in response.text
    assert "hunk_count=3" in response.text
    assert "Validation Results" in response.text
    assert "pytest_auth" in response.text
    assert "failed" in response.text
    assert "output_bytes=12" in response.text
    assert "error_lines=2" in response.text
    assert 'href="/console/context?run_id=run-alpha"' in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_actions_renders_empty_state_without_run_id() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/actions")

    assert response.status_code == 200
    assert "Action Executions" in response.text
    assert "Validation Results" in response.text
    assert "No action summaries are available." in response.text
    assert "No validation summaries are available." in response.text


@pytest.mark.asyncio
async def test_console_memory_and_actions_restrict_user_token_to_visible_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CODING_AGENT_SERVER_CONFIG",
        str(_write_console_auth_config(tmp_path)),
    )
    monkeypatch.setattr(settings, "http_api_key", None)
    _register_console_session(
        "session-user",
        owner_label=_owner_label("user-token-a"),
    )
    _register_console_session(
        "session-admin",
        owner_label=_owner_label("admin-token"),
    )
    session_manager.configure_runtime_store(
        _ConsoleRuntimeStore([
            _runtime_run("run-user", "session-user", status="completed"),
            _runtime_run("run-admin", "session-admin", status="completed"),
        ])
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        memory = await client.get(
            "/console/memory",
            params={"run_id": "run-admin"},
            headers={"Authorization": "Bearer user-token-a"},
        )
        actions = await client.get(
            "/console/actions",
            params={"run_id": "run-admin"},
            headers={"Authorization": "Bearer user-token-a"},
        )

    assert memory.status_code == 200
    assert "memory-auth-policy" not in memory.text
    assert "No memory evidence is available." in memory.text
    assert actions.status_code == 200
    assert "action-alpha" not in actions.text
    assert "No action summaries are available." in actions.text


@pytest.mark.asyncio
async def test_console_interactions_renders_pending_and_resolved_lists() -> None:
    _register_console_session("session-alpha")
    store = _ConsoleRuntimeStore([
        _runtime_run("run-alpha", "session-alpha", status="running")
    ])
    await store.create_agent_interaction(
        _interaction("interaction-pending", "run-alpha", status="pending")
    )
    await store.create_agent_interaction(
        _interaction(
            "interaction-approved",
            "run-alpha",
            status="approved",
            resolved=True,
        )
    )
    session_manager.configure_runtime_store(store)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/interactions")

    assert response.status_code == 200
    assert "Pending Interactions" in response.text
    assert "Resolved Interactions" in response.text
    assert "interaction-pending" in response.text
    assert "interaction-approved" in response.text
    assert "run-alpha" in response.text
    assert "session-alpha" in response.text
    assert "approval" in response.text
    assert "pending" in response.text
    assert "approved" in response.text
    assert "tool-call-visible" in response.text
    assert 'href="/console/runs/run-alpha"' in response.text
    assert "tool-secret" not in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_interactions_displays_terminal_duplicate_state_safely() -> None:
    _register_console_session("session-alpha")
    store = _ConsoleRuntimeStore([
        _runtime_run("run-alpha", "session-alpha", status="completed")
    ])
    await store.create_agent_interaction(
        _interaction(
            "interaction-terminal",
            "run-alpha",
            status="duplicate_terminal",
            resolved=True,
        )
    )
    session_manager.configure_runtime_store(store)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/interactions")

    assert response.status_code == 200
    assert "interaction-terminal" in response.text
    assert "duplicate_terminal" in response.text
    assert "Resolved Interactions" in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_run_detail_renders_completed_run_without_raw_snapshot() -> None:
    await _configure_run_detail_fixture()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/runs/run-detail")

    assert response.status_code == 200
    assert "Run Detail" in response.text
    assert "run-detail" in response.text
    assert "session-detail" in response.text
    assert "completed" in response.text
    assert "Message Snapshot" in response.text
    assert "2 messages" in response.text
    assert "role:user" in response.text
    assert "role:assistant" in response.text
    assert "Runtime Events" in response.text
    assert "wire.StreamDelta" in response.text
    assert "wire.TurnEnd" in response.text
    assert response.text.index("wire.StreamDelta") < response.text.index("wire.TurnEnd")
    assert "last_event_id" in response.text
    assert "/runs/run-detail/events" in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text
    assert "tool-secret" not in response.text


@pytest.mark.asyncio
async def test_console_run_detail_renders_failed_run_with_safe_error_summary() -> None:
    await _configure_run_detail_fixture(status="failed", error="safe failure summary")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/runs/run-detail")

    assert response.status_code == 200
    assert "failed" in response.text
    assert "safe failure summary" in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_run_detail_redacts_sensitive_message_labels() -> None:
    store = await _configure_run_detail_fixture()
    await store.save_message_snapshot(
        RunMessageSnapshotRecord(
            snapshot_id="run-detail:latest",
            run_id="run-detail",
            messages=[
                {"role": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT"},
                {"message_type": "command_output"},
            ],
            metadata={},
            created_at=datetime(2026, 5, 20, 2, 0, 10, tzinfo=UTC),
        )
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/runs/run-detail")

    assert response.status_code == 200
    assert "<li>message</li>" in response.text
    assert "role:SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT" not in response.text
    assert "type:command_output" not in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_run_detail_renders_running_run_without_finished_time() -> None:
    await _configure_run_detail_fixture(status="running")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/runs/run-detail")

    assert response.status_code == 200
    assert "running" in response.text
    assert "<dt>Finished</dt><dd>-</dd>" in response.text


@pytest.mark.asyncio
async def test_console_run_detail_redacts_sensitive_error_summary() -> None:
    await _configure_run_detail_fixture(
        status="failed",
        error="SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT stdout",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/runs/run-detail")

    assert response.status_code == 200
    assert "Sensitive error summary redacted." in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_runs_list_renders_fixture_data_and_status_filter() -> None:
    _register_console_session("session-alpha")
    _register_console_session("session-beta")
    session_manager.configure_runtime_store(
        _ConsoleRuntimeStore([
            _runtime_run("run-complete", "session-alpha", status="completed"),
            _runtime_run(
                "run-failed",
                "session-beta",
                status="failed",
                error="safe failure summary",
            ),
            _runtime_run("run-running", "session-alpha", status="running"),
        ])
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/runs")
        failed_response = await client.get("/console/runs", params={"status": "failed"})

    assert response.status_code == 200
    assert "run-complete" in response.text
    assert "run-failed" in response.text
    assert "run-running" in response.text
    assert 'href="/console/runs/run-failed"' in response.text
    assert "safe failure summary" in response.text

    assert failed_response.status_code == 200
    assert "run-failed" in failed_response.text
    assert "run-complete" not in failed_response.text
    assert "run-running" not in failed_response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text
        assert forbidden not in failed_response.text


@pytest.mark.asyncio
async def test_console_runs_list_redacts_sensitive_error_summary() -> None:
    _register_console_session("session-sensitive")
    session_manager.configure_runtime_store(
        _ConsoleRuntimeStore([
            _runtime_run(
                "run-sensitive",
                "session-sensitive",
                status="failed",
                error="SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT command_output",
            )
        ])
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/runs")

    assert response.status_code == 200
    assert "run-sensitive" in response.text
    assert "Sensitive error summary redacted." in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text
