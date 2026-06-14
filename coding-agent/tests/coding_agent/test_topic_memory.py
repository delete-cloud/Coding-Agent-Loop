from __future__ import annotations

from datetime import UTC, datetime

from coding_agent.bee.workspace import (
    BeeWorkspaceExecutorRunArtifact,
    BeeWorkspaceRunArtifacts,
    BeeWorkspaceRunNode,
)
from coding_agent.topics.memory import (
    MEMORY_CANDIDATE_STATUS,
    MEMORY_REFERENCE_MODE,
    propose_memory_candidate_from_topic,
    propose_memory_candidates_from_bee_artifacts,
)
from coding_agent.topics.store import TopicRecord


def test_topic_finalization_creates_memory_candidate_with_provenance() -> None:
    candidate = propose_memory_candidate_from_topic(
        _topic(
            "topic-auth",
            title="Auth cleanup",
            summary="JWT validation moved to shared middleware",
        ),
        kind="decision",
        tags=("auth", "jwt"),
        confidence=0.7,
    )

    assert candidate is not None
    payload = candidate.to_dict()
    assert payload["kind"] == "decision"
    assert payload["status"] == MEMORY_CANDIDATE_STATUS
    assert payload["reference_mode"] == MEMORY_REFERENCE_MODE
    assert payload["summary"] == "JWT validation moved to shared middleware"
    assert payload["provenance"] == {
        "topic_id": "topic-auth",
        "topic_status": "finalized",
        "topic_kind": "coding",
        "source_entry_ranges": [
            {"topic_id": "topic-auth", "start_seq": 2, "end_seq": 9}
        ],
    }


def test_open_or_summaryless_topic_does_not_create_candidate() -> None:
    assert (
        propose_memory_candidate_from_topic(
            _topic("topic-open", status="open", summary="Still changing", end=None)
        )
        is None
    )
    assert (
        propose_memory_candidate_from_topic(_topic("topic-empty", summary=None)) is None
    )


def test_bee_task_report_creates_memory_candidate_with_evidence_refs() -> None:
    candidates = propose_memory_candidates_from_bee_artifacts(
        topic=_topic("topic-bee", summary="Backup validation finished"),
        artifacts=_bee_artifacts(),
    )

    assert len(candidates) == 2
    report_candidate = candidates[0].to_dict()
    assert report_candidate["kind"] == "procedure"
    assert report_candidate["title"] == "Backup check completed"
    assert report_candidate["summary"] == "Validation passed with sanitized evidence."
    assert report_candidate["status"] == "candidate"
    assert report_candidate["provenance"] == {
        "topic_id": "topic-bee",
        "topic_status": "finalized",
        "topic_kind": "coding",
        "source_entry_ranges": [
            {"topic_id": "topic-bee", "start_seq": 2, "end_seq": 9}
        ],
        "task_id": "bee-task-alpha",
        "run_id": "run-alpha",
        "report_refs": ["report.md"],
        "evidence_refs": ["evidence/executor-run-alpha.md"],
        "template_id": "backup-check",
    }

    existing_candidate = candidates[1].to_dict()
    assert existing_candidate["kind"] == "project_convention"
    assert existing_candidate["summary"] == "Backups should be checked before restore."
    assert existing_candidate["tags"] == ["backup-check", "restore"]


def test_bee_memory_candidate_includes_pack_template_provenance() -> None:
    candidates = propose_memory_candidates_from_bee_artifacts(
        topic=_topic("topic-bee", summary="Backup validation finished"),
        artifacts=_bee_artifacts(),
        pack_id="pack-alpha",
        domain_profile="maintenance",
        pack_tags=("homelab", "backup"),
    )

    payload = candidates[0].to_dict()

    assert payload["tags"] == ["backup", "backup-check", "homelab", "pack-alpha"]
    assert payload["reference_mode"] == MEMORY_REFERENCE_MODE
    assert payload["provenance"]["pack_id"] == "pack-alpha"
    assert payload["provenance"]["template_id"] == "backup-check"
    assert payload["provenance"]["domain_profile"] == "maintenance"
    assert payload["provenance"]["pack_tags"] == ["backup", "homelab"]


def test_bee_candidate_generation_rejects_mismatched_topic() -> None:
    try:
        propose_memory_candidates_from_bee_artifacts(
            topic=_topic("topic-other", summary="Other topic"),
            artifacts=_bee_artifacts(),
        )
    except ValueError as exc:
        assert str(exc) == "Bee artifact topic_id must match topic"
    else:
        raise AssertionError("expected mismatched topic to fail")


def test_noisy_existing_memory_candidate_is_skipped() -> None:
    artifacts = _bee_artifacts(
        memory_candidates=(
            {"kind": "fact", "summary": "stdout: raw command output"},
            {"kind": "fact"},
        )
    )

    candidates = propose_memory_candidates_from_bee_artifacts(
        topic=_topic("topic-bee", summary="Backup validation finished"),
        artifacts=artifacts,
    )

    assert len(candidates) == 1
    assert candidates[0].summary == "Validation passed with sanitized evidence."


def test_raw_report_summary_is_rejected() -> None:
    artifacts = _bee_artifacts(report_summary="command: pytest -q")

    try:
        propose_memory_candidates_from_bee_artifacts(
            topic=_topic("topic-bee", summary="Backup validation finished"),
            artifacts=artifacts,
        )
    except ValueError as exc:
        assert "forbidden raw content marker" in str(exc)
    else:
        raise AssertionError("expected raw report summary to fail")


def _bee_artifacts(
    *,
    report_summary: str = "Validation passed with sanitized evidence.",
    memory_candidates: tuple[dict[str, object], ...] = (
        {
            "kind": "project_convention",
            "title": "Backup restore convention",
            "summary": "Backups should be checked before restore.",
            "tags": ["restore"],
            "confidence": 0.8,
        },
    ),
) -> BeeWorkspaceRunArtifacts:
    return BeeWorkspaceRunArtifacts(
        task_id="bee-task-alpha",
        template_id="backup-check",
        topic_id="topic-bee",
        status="completed",
        nodes=(
            BeeWorkspaceRunNode(
                node_id="node-validate",
                status="completed",
                run_id="run-alpha",
                validation_ids=("validation-alpha",),
                attempts=1,
            ),
        ),
        run_ids=("run-alpha",),
        validation_ids=("validation-alpha",),
        report_title="Backup check completed",
        report_summary=report_summary,
        executor_runs=(
            BeeWorkspaceExecutorRunArtifact(
                executor_run_id="executor-run-alpha",
                executor_kind="local",
                status="succeeded",
                executor_summary="Local executor succeeded",
                task_id="bee-task-alpha",
                node_id="node-validate",
                topic_id="topic-bee",
                executor_evidence_path="evidence/executor-run-alpha.md",
            ),
        ),
        memory_candidates=memory_candidates,
    )


def _topic(
    topic_id: str,
    *,
    title: str | None = "Topic",
    summary: str | None,
    status: str = "finalized",
    start: int = 2,
    end: int | None = 9,
) -> TopicRecord:
    return TopicRecord(
        topic_id=topic_id,
        tape_id="tape-1",
        session_id="session-1",
        kind="coding",
        status=status,
        title=title,
        summary=summary,
        owner="local",
        topic_initial_seq=start,
        topic_finalized_seq=end,
        created_at=datetime(2026, 5, 23, 9, 1, tzinfo=UTC),
        finalized_at=datetime(2026, 5, 23, 9, 2, tzinfo=UTC)
        if status == "finalized"
        else None,
        metadata={"profile": "local"},
    )
