from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from agentkit.observability import SpanRecord
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from coding_agent.observability import (
    PrometheusMetricsObservationSink,
    PrometheusMetricsRecorder,
)
from coding_agent.topic_lifecycle import (
    TOPIC_FINALIZED,
    TOPIC_INITIAL,
    TopicLifecycle,
    find_topic_anchors,
    topic_range_entries,
)
from coding_agent.topic_provenance import (
    topic_cost_delta,
    topic_eval_provenance,
    topic_memory_provenance,
    topic_metric_attributes,
)
from coding_agent.topic_recall import (
    RECALL_ANCHOR,
    recall_topic_summaries,
    record_topic_recall,
    topic_recall_context_pack,
)
from coding_agent.topic_store import (
    JSONObject,
    TopicAnchorRecord,
    TopicCostRecord,
    TopicRecallLinkRecord,
    TopicRecord,
)
from coding_agent.server.developer_console import (
    ConsoleTopicAnchorSummary,
    ConsoleTopicCostSummary,
    ConsoleTopicDetail,
    ConsoleTopicRecallSummary,
    ConsoleTopicSummary,
    render_console_topic_detail_page,
    render_console_topics_page,
)


class FakeTopicLayerStore:
    def __init__(self) -> None:
        self.topics: dict[str, TopicRecord] = {}
        self.anchors: list[TopicAnchorRecord] = []
        self.recalls: list[TopicRecallLinkRecord] = []
        self.costs: dict[str, TopicCostRecord] = {}

    async def create_topic(self, record: TopicRecord) -> TopicRecord:
        self.topics.setdefault(record.topic_id, record)
        return self.topics[record.topic_id]

    async def finalize_topic(
        self,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int,
        finalized_at: datetime,
        metadata: JSONObject,
    ) -> TopicRecord:
        topic = self.topics[topic_id]
        finalized = replace(
            topic,
            status="finalized",
            summary=summary,
            topic_finalized_seq=topic_finalized_seq,
            finalized_at=finalized_at,
            metadata=metadata,
        )
        self.topics[topic_id] = finalized
        return finalized

    async def abort_topic(
        self,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int | None,
        finalized_at: datetime,
        metadata: JSONObject,
    ) -> TopicRecord:
        topic = self.topics[topic_id]
        aborted = replace(
            topic,
            status="aborted",
            summary=summary,
            topic_finalized_seq=topic_finalized_seq,
            finalized_at=finalized_at,
            metadata=metadata,
        )
        self.topics[topic_id] = aborted
        return aborted

    async def record_topic_anchor(
        self,
        record: TopicAnchorRecord,
    ) -> TopicAnchorRecord:
        self.anchors.append(record)
        return record

    async def record_recall_link(
        self,
        record: TopicRecallLinkRecord,
    ) -> TopicRecallLinkRecord:
        self.recalls.append(record)
        return record

    async def update_topic_cost(self, delta: TopicCostRecord) -> TopicCostRecord:
        current = self.costs.get(delta.topic_id)
        if current is None:
            self.costs[delta.topic_id] = delta
            return delta
        updated = TopicCostRecord(
            topic_id=delta.topic_id,
            prompt_tokens=current.prompt_tokens + delta.prompt_tokens,
            completion_tokens=current.completion_tokens + delta.completion_tokens,
            total_tokens=current.total_tokens + delta.total_tokens,
            run_count=current.run_count + delta.run_count,
            action_count=current.action_count + delta.action_count,
            validation_count=current.validation_count + delta.validation_count,
            tool_call_count=current.tool_call_count + delta.tool_call_count,
            metadata=delta.metadata,
        )
        self.costs[delta.topic_id] = updated
        return updated


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 5, 21, 9, tzinfo=UTC)

    def __call__(self) -> datetime:
        value = self.now
        self.now += timedelta(minutes=1)
        return value


@pytest.mark.asyncio
async def test_topic_layer_lifecycle_recall_provenance_console_and_metrics_smoke() -> (
    None
):
    store = FakeTopicLayerStore()
    ids = iter(("topic-auth", "topic-followup"))
    lifecycle = TopicLifecycle(
        store=store,
        now=FakeClock(),
        topic_id_factory=lambda: next(ids),
    )
    tape = Tape(tape_id="tape-alpha")

    first = await lifecycle.create_topic(
        tape=tape,
        session_id="session-alpha",
        kind="coding",
        title="JWT validation",
        owner="local",
        metadata={"profile": "local"},
    )
    tape.append(Entry(kind="event", payload={"kind": "run"}))
    finalized = await lifecycle.finalize_topic(
        tape=tape,
        topic=first,
        summary="JWT validation moved safely",
        metadata={"status_reason": "completed"},
    )

    anchors = find_topic_anchors(tape)
    assert [anchor.product_anchor_type for anchor in anchors] == [
        TOPIC_INITIAL,
        TOPIC_FINALIZED,
    ]
    assert [entry.kind for entry in topic_range_entries(tape, finalized)] == [
        "anchor",
        "event",
        "anchor",
    ]

    followup = await lifecycle.create_topic(
        tape=tape,
        session_id="session-alpha",
        kind="coding",
        title="JWT cleanup",
        owner="local",
        metadata={"profile": "local"},
    )
    recalled = recall_topic_summaries(
        source_topic=followup,
        candidates=[finalized],
        query="jwt validation",
    )
    assert [item.topic.topic_id for item in recalled] == ["topic-auth"]

    link = await record_topic_recall(
        tape=tape,
        store=store,
        source_topic=followup,
        recalled=recalled[0],
    )
    assert tape[-1].meta["product_anchor_type"] == RECALL_ANCHOR
    assert link.recalled_topic_id == "topic-auth"
    assert store.recalls == [link]

    context_pack = topic_recall_context_pack(recalled, enabled=True)
    context_item = context_pack.to_dict()["sections"][0]["items"][0]
    assert context_item["metadata"]["source_topic_ids"] == ["topic-auth"]
    assert context_item["metadata"]["source_entry_ranges"] == [
        {"topic_id": "topic-auth", "start_seq": 0, "end_seq": 2}
    ]

    eval_provenance = topic_eval_provenance(topic=finalized)
    memory_provenance = topic_memory_provenance(topic=finalized)
    assert eval_provenance["topic_id"] == "topic-auth"
    assert memory_provenance["source_entry_ranges"][0]["topic_id"] == "topic-auth"

    cost = await store.update_topic_cost(
        topic_cost_delta(
            topic_id="topic-auth",
            prompt_tokens=10,
            completion_tokens=7,
            run_count=1,
            action_count=1,
            validation_count=1,
            tool_call_count=2,
        )
    )
    assert cost.total_tokens == 17

    topic_summary = ConsoleTopicSummary(
        topic_id="topic-auth",
        tape_id="tape-alpha",
        session_id="session-alpha",
        kind="coding",
        status="finalized",
        title="JWT validation",
        summary="JWT validation moved safely",
        topic_initial_seq=finalized.topic_initial_seq,
        topic_finalized_seq=finalized.topic_finalized_seq,
        run_count=1,
        cost_total_tokens=17,
    )
    list_html = render_console_topics_page([topic_summary])
    followup_summary = ConsoleTopicSummary(
        topic_id="topic-followup",
        tape_id="tape-alpha",
        session_id="session-alpha",
        kind="coding",
        status="open",
        title="JWT cleanup",
        summary=None,
        topic_initial_seq=followup.topic_initial_seq,
        topic_finalized_seq=followup.topic_finalized_seq,
        run_count=0,
        cost_total_tokens=None,
    )
    auth_detail_html = render_console_topic_detail_page(
        ConsoleTopicDetail(
            summary=topic_summary,
            anchors=(
                ConsoleTopicAnchorSummary(0, TOPIC_INITIAL, "entry-topic-start"),
                ConsoleTopicAnchorSummary(2, TOPIC_FINALIZED, "entry-topic-end"),
            ),
            recalls=(),
            cost=ConsoleTopicCostSummary(
                prompt_tokens=10,
                completion_tokens=7,
                total_tokens=17,
                run_count=1,
                action_count=1,
                validation_count=1,
                tool_call_count=2,
            ),
            runs=(),
            actions=(),
            validations=(),
        )
    )
    followup_detail_html = render_console_topic_detail_page(
        ConsoleTopicDetail(
            summary=followup_summary,
            anchors=(
                ConsoleTopicAnchorSummary(
                    followup.topic_initial_seq,
                    TOPIC_INITIAL,
                    "entry-followup-start",
                ),
            ),
            recalls=(
                ConsoleTopicRecallSummary(
                    recalled_topic_id="topic-auth",
                    relation="summary_recall",
                    anchor_seq=link.anchor_seq,
                ),
            ),
            cost=None,
            runs=(),
            actions=(),
            validations=(),
        )
    )
    assert "Topic List" in list_html
    assert "Topic Range Summary" in auth_detail_html
    assert "Topic Cost" in auth_detail_html
    assert "topic-auth" not in auth_detail_html.split("Recall Links", maxsplit=1)[-1]
    assert "Recall Links" in followup_detail_html
    assert "topic-auth" in followup_detail_html
    for forbidden in ("raw prompt", "command_output", "stdout", "stderr", "secret"):
        assert forbidden not in list_html
        assert forbidden not in auth_detail_html
        assert forbidden not in followup_detail_html

    recorder = PrometheusMetricsRecorder()
    sink = PrometheusMetricsObservationSink(recorder=recorder)
    sink.record_span(
        SpanRecord(
            name="context_pack.build",
            status="ok",
            attributes=topic_metric_attributes(topic=finalized, profile="local")
            | {"topic_id": finalized.topic_id},
            duration_ms=1,
        )
    )
    metrics = recorder.exposition_text()
    assert 'topic_kind="coding"' in metrics
    assert 'topic_status="finalized"' in metrics
    assert "topic_id" not in metrics
    assert "topic-auth" not in metrics
