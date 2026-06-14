from __future__ import annotations

from datetime import UTC, datetime

from agentkit.tape.tape import Tape

from coding_agent.bee.workspace import (
    BeeWorkspaceExecutorRunArtifact,
    BeeWorkspaceRunArtifacts,
    BeeWorkspaceRunNode,
)
from coding_agent.observability import PrometheusMetricsRecorder
from coding_agent.topics.recall_context import (
    TopicRecallPlanner,
    TopicRecallPlannerInput,
    recall_context_pack,
    record_recall_plan,
)
from coding_agent.topics.memory import (
    MemoryReviewStore,
    propose_memory_candidates_from_bee_artifacts,
)
from coding_agent.topics.range_index import TopicRangeIndex
from coding_agent.topics.store import TopicRecallLinkRecord, TopicRecord
from coding_agent.server.developer_console import (
    ConsoleMemoryReviewSummary,
    ConsoleMemorySummary,
    ConsoleTopicAnchorSummary,
    ConsoleTopicDetail,
    ConsoleTopicRecallSummary,
    ConsoleTopicSummary,
    render_console_memory_page,
    render_console_topic_detail_page,
)


SENSITIVE_SENTINEL = "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT"


class FakeRecallStore:
    def __init__(self) -> None:
        self.links: list[TopicRecallLinkRecord] = []

    async def record_recall_link(
        self,
        record: TopicRecallLinkRecord,
    ) -> TopicRecallLinkRecord:
        self.links.append(record)
        return record


async def test_cross_topic_memory_e2e_smoke() -> None:
    prior_topic = _topic(
        "topic-backup",
        title="Backup validation",
        summary="Backup validation passed with sanitized executor evidence",
    )
    index = TopicRangeIndex()
    indexed = index.index_topic(
        prior_topic,
        profile="local",
        tags=("backup", "restore"),
        bee_template_id="backup-check",
        related_task_ids=("bee-task-backup",),
        report_refs=("report.md",),
        evidence_refs=("evidence/executor-run-alpha.md",),
        report_summary="Validation passed with sanitized evidence.",
        evidence_summaries=("Executor finished with sanitized summary.",),
    )
    assert indexed is not None

    candidates = propose_memory_candidates_from_bee_artifacts(
        topic=prior_topic,
        artifacts=_bee_artifacts(),
    )
    review_store = MemoryReviewStore()
    reviewed = [review_store.add_candidate(candidate) for candidate in candidates]
    accepted = review_store.accept_candidate(reviewed[0].candidate.candidate_id or "")
    duplicate = review_store.accept_candidate(accepted.candidate.candidate_id or "")
    assert duplicate == accepted

    new_topic = _topic(
        "topic-new",
        title="Restore readiness",
        summary=None,
        status="open",
        end=None,
    )
    plan = TopicRecallPlanner(
        topic_index=index,
        accepted_memories=review_store.accepted_memories(),
    ).plan(
        TopicRecallPlannerInput(
            source_topic=new_topic,
            text="backup validation restore",
            profile="local",
            bee_template_id="backup-check",
            tags=("backup", "restore"),
        )
    )

    assert [result.topic_id for result in plan.topic_results] == ["topic-backup"]
    assert [memory.candidate.candidate_id for memory in plan.accepted_memories] == [
        accepted.candidate.candidate_id
    ]

    tape = Tape(tape_id=new_topic.tape_id)
    store = FakeRecallStore()
    links = await record_recall_plan(tape=tape, store=store, plan=plan)
    context_pack = recall_context_pack(plan)
    payload = context_pack.to_dict()

    assert len(links) == 1
    assert getattr(tape[-1], "meta")["product_anchor_type"] == "recall_anchor"
    assert store.links == list(links)
    assert payload["sections"][0]["title"] == "Cross-topic recall references"
    assert payload["sections"][1]["title"] == "Accepted memory references"
    assert payload["sections"][1]["items"][0]["metadata"]["reference_mode"] == (
        "reference_only"
    )

    memory_html = render_console_memory_page(
        ConsoleMemorySummary(
            run_id=None,
            items=(),
            reviews=(
                ConsoleMemoryReviewSummary(
                    source_id=accepted.candidate.candidate_id or "missing",
                    label=accepted.candidate.title,
                    kind=accepted.candidate.kind,
                    status=accepted.status,
                    run_id="run-alpha",
                    topic_id="topic-backup",
                    task_id="bee-task-backup",
                    evidence_count=1,
                    source_range_count=1,
                ),
            ),
        )
    )
    topic_html = render_console_topic_detail_page(
        ConsoleTopicDetail(
            summary=ConsoleTopicSummary(
                topic_id="topic-new",
                tape_id="tape-1",
                session_id="session-1",
                kind="coding",
                status="open",
                title="Restore readiness",
                summary=None,
                topic_initial_seq=10,
                topic_finalized_seq=None,
                run_count=1,
                cost_total_tokens=None,
            ),
            anchors=(
                ConsoleTopicAnchorSummary(
                    seq=1, anchor_type="recall_anchor", entry_id=None
                ),
            ),
            recalls=(
                ConsoleTopicRecallSummary(
                    recalled_topic_id="topic-backup",
                    relation="summary_recall",
                    anchor_seq=1,
                ),
            ),
            cost=None,
            runs=(),
            actions=(),
            validations=(),
        )
    )

    assert "Memory Review Inbox" in memory_html
    assert "topic-backup" in memory_html
    assert "bee-task-backup" in memory_html
    assert "Recall Links" in topic_html
    assert "topic-backup" in topic_html

    recorder = PrometheusMetricsRecorder()
    recorder.record_topic_recall_run(
        source="topic_and_memory",
        status="matched",
        candidate_count=2,
    )
    recorder.record_memory_candidate(kind="procedure", status="candidate")
    recorder.record_memory_review(status="accepted")
    metrics = recorder.exposition_text()

    assert (
        'topic_recall_runs_total{source="topic_and_memory",status="matched"} 1'
        in metrics
    )
    assert 'memory_candidates_total{kind="procedure",status="candidate"} 1' in metrics
    assert 'memory_reviews_total{status="accepted"} 1' in metrics
    for rendered in (memory_html, topic_html, metrics, repr(payload)):
        _assert_no_sensitive_content(rendered)


def _bee_artifacts() -> BeeWorkspaceRunArtifacts:
    return BeeWorkspaceRunArtifacts(
        task_id="bee-task-backup",
        template_id="backup-check",
        topic_id="topic-backup",
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
        report_summary="Validation passed with sanitized evidence.",
        executor_runs=(
            BeeWorkspaceExecutorRunArtifact(
                executor_run_id="executor-run-alpha",
                executor_kind="local",
                status="succeeded",
                executor_summary="Local executor succeeded",
                task_id="bee-task-backup",
                node_id="node-validate",
                topic_id="topic-backup",
                executor_evidence_path="evidence/executor-run-alpha.md",
            ),
        ),
        memory_candidates=(
            {
                "kind": "project_convention",
                "title": "Backup restore convention",
                "summary": "Backups should be checked before restore.",
                "tags": ["restore"],
                "confidence": 0.8,
            },
        ),
    )


def _topic(
    topic_id: str,
    *,
    title: str | None,
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


def _assert_no_sensitive_content(value: str) -> None:
    for forbidden in (
        SENSITIVE_SENTINEL,
        "prompt",
        "message",
        "command_output",
        "stdout",
        "stderr",
        "env",
        "secret",
        "raw log",
    ):
        assert forbidden not in value
