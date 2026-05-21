from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from agentkit.storage.pg import AsyncPGPool, PGPool
from agentkit.tape.tape import Tape
from coding_agent.scheduled_runs import (
    PGScheduledRunStore,
    ProactiveSignalPlanner,
    ProactiveSignalRecord,
    ScheduleRecord,
    ScheduleTriggerRecord,
    ScheduledLaunchIntent,
    ScheduledRunLaunchPreparer,
    ScheduledRunPlanner,
)
from coding_agent.topic_lifecycle import TopicLifecycle
from coding_agent.topic_store import JSONObject, TopicAnchorRecord, TopicRecord


class FakeScheduledPool:
    def __init__(self) -> None:
        self.schedules: dict[str, dict[str, object]] = {}
        self.triggers: dict[str, dict[str, object]] = {}
        self.signals: dict[str, dict[str, object]] = {}
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, *args: object) -> str:
        self.executed.append((query, args))
        if "CREATE TABLE IF NOT EXISTS scheduled_runs" in query:
            return "CREATE TABLE"
        raise AssertionError(f"unexpected execute query: {query}")

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        self.executed.append((query, args))
        if "INSERT INTO scheduled_runs" in query:
            row = _schedule_row(*args)
            self.schedules[cast(str, row["schedule_id"])] = row
            return row
        if "SELECT * FROM scheduled_runs WHERE schedule_id = $1" in query:
            return self.schedules.get(cast(str, args[0]))
        if "UPDATE scheduled_runs" in query:
            return self._update_schedule(args)
        if "INSERT INTO scheduled_run_triggers" in query:
            row = _trigger_row(*args)
            self.triggers[cast(str, row["trigger_id"])] = row
            return row
        if "INSERT INTO proactive_signals" in query:
            return self._insert_signal(args)
        if "SELECT * FROM proactive_signals WHERE signal_id = $1" in query:
            return self.signals.get(cast(str, args[0]))
        if "UPDATE proactive_signals" in query:
            return self._update_signal(args)
        raise AssertionError(f"unexpected fetchrow query: {query}")

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.executed.append((query, args))
        if "SELECT * FROM scheduled_runs" in query:
            if "next_due_at <= $1" in query:
                due_at_or_before, limit = args
                rows = [
                    row
                    for row in self.schedules.values()
                    if row["status"] == "active"
                    and row["next_due_at"] is not None
                    and cast(datetime, row["next_due_at"])
                    <= cast(datetime, due_at_or_before)
                ]
                rows.sort(
                    key=lambda row: (
                        cast(datetime, row["next_due_at"]),
                        row["schedule_id"],
                    )
                )
                return rows[: cast(int, limit)]
            session_id, topic_id, status, limit = args
            rows = [
                row
                for row in self.schedules.values()
                if (session_id is None or row["session_id"] == session_id)
                and (topic_id is None or row["topic_id"] == topic_id)
                and (status is None or row["status"] == status)
            ]
            rows.sort(
                key=lambda row: (
                    cast(datetime, row["next_due_at"] or row["updated_at"]),
                    row["schedule_id"],
                )
            )
            return rows[: cast(int, limit)]
        if "SELECT * FROM scheduled_run_triggers" in query:
            schedule_id, limit = args
            rows = [
                row
                for row in self.triggers.values()
                if row["schedule_id"] == schedule_id
            ]
            rows.sort(
                key=lambda row: (cast(datetime, row["due_at"]), row["trigger_id"])
            )
            return rows[: cast(int, limit)]
        if "SELECT * FROM proactive_signals" in query:
            status, session_id, topic_id, limit = args
            rows = [
                row
                for row in self.signals.values()
                if (status is None or row["status"] == status)
                and (session_id is None or row["session_id"] == session_id)
                and (topic_id is None or row["topic_id"] == topic_id)
            ]
            rows.sort(
                key=lambda row: (cast(datetime, row["observed_at"]), row["signal_id"])
            )
            return rows[: cast(int, limit)]
        raise AssertionError(f"unexpected fetch query: {query}")

    async def close(self) -> None:
        return None

    async def acquire(self) -> FakeScheduledPool:
        return self

    async def release(self, connection: object) -> None:
        if connection is not self:
            raise AssertionError("unexpected connection released")

    def _update_schedule(self, args: tuple[object, ...]) -> dict[str, object] | None:
        schedule_id, status, next_due_at, last_triggered_at, updated_at, metadata = args
        row = self.schedules.get(cast(str, schedule_id))
        if row is None:
            return None
        row.update(
            {
                "status": status,
                "next_due_at": next_due_at,
                "last_triggered_at": last_triggered_at,
                "updated_at": updated_at,
                "metadata": metadata,
            }
        )
        return row

    def _insert_signal(self, args: tuple[object, ...]) -> dict[str, object]:
        row = _signal_row(*args)
        existing = next(
            (
                item
                for item in self.signals.values()
                if item["dedupe_key"] == row["dedupe_key"]
            ),
            None,
        )
        if existing is not None:
            return existing
        self.signals[cast(str, row["signal_id"])] = row
        return row

    def _update_signal(self, args: tuple[object, ...]) -> dict[str, object] | None:
        signal_id, status, cooldown_until, metadata = args
        row = self.signals.get(cast(str, signal_id))
        if row is None:
            return None
        row.update(
            {
                "status": status,
                "cooldown_until": cooldown_until,
                "metadata": metadata,
            }
        )
        return row


class FakeScheduledTopicStore:
    def __init__(self) -> None:
        self.topics: dict[str, TopicRecord] = {}
        self.anchors: list[TopicAnchorRecord] = []

    async def create_topic(self, record: TopicRecord) -> TopicRecord:
        self.topics[record.topic_id] = record
        return record

    async def load_topic(self, topic_id: str) -> TopicRecord | None:
        return self.topics.get(topic_id)

    async def find_open_topic(
        self,
        *,
        session_id: str,
        tape_id: str,
    ) -> TopicRecord | None:
        for topic in self.topics.values():
            if (
                topic.session_id == session_id
                and topic.tape_id == tape_id
                and topic.status == "open"
            ):
                return topic
        return None

    async def finalize_topic(
        self,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int,
        finalized_at: datetime,
        metadata: JSONObject,
    ) -> TopicRecord:
        raise AssertionError("scheduled launch preparation must not finalize topics")

    async def abort_topic(
        self,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int | None,
        finalized_at: datetime,
        metadata: JSONObject,
    ) -> TopicRecord:
        raise AssertionError("scheduled launch preparation must not abort topics")

    async def record_topic_anchor(
        self,
        record: TopicAnchorRecord,
    ) -> TopicAnchorRecord:
        self.anchors.append(record)
        return record


@pytest.fixture
def fake_pool() -> FakeScheduledPool:
    return FakeScheduledPool()


@pytest.fixture
def store(fake_pool: FakeScheduledPool) -> PGScheduledRunStore:
    async def fake_pool_factory(**_: object) -> AsyncPGPool:
        return cast(AsyncPGPool, fake_pool)

    return PGScheduledRunStore(
        pool=PGPool(dsn="postgresql://example", pool_factory=fake_pool_factory)
    )


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 5, 21, hour, minute, tzinfo=UTC)


def _schedule(schedule_id: str = "schedule-1") -> ScheduleRecord:
    return ScheduleRecord(
        schedule_id=schedule_id,
        session_id="session-1",
        topic_id="topic-1",
        kind="interval",
        status="active",
        cadence="daily",
        owner="local",
        title="Daily safe check",
        next_due_at=_dt(10),
        last_triggered_at=None,
        created_at=_dt(9),
        updated_at=_dt(9),
        metadata={"profile": "local"},
    )


def _schedule_with_due(
    schedule_id: str,
    *,
    next_due_at: datetime | None,
    cadence: str = "daily",
    status: str = "active",
) -> ScheduleRecord:
    return ScheduleRecord(
        schedule_id=schedule_id,
        session_id="session-1",
        topic_id="topic-1",
        kind="interval",
        status=status,
        cadence=cadence,
        owner="local",
        title="Daily safe check",
        next_due_at=next_due_at,
        last_triggered_at=None,
        created_at=_dt(9),
        updated_at=_dt(9),
        metadata={"profile": "local"},
    )


def _trigger(trigger_id: str = "trigger-1") -> ScheduleTriggerRecord:
    return ScheduleTriggerRecord(
        trigger_id=trigger_id,
        schedule_id="schedule-1",
        signal_id="signal-1",
        topic_id="topic-1",
        run_id=None,
        status="planned",
        due_at=_dt(10),
        planned_at=_dt(9, 30),
        reason="due",
        metadata={"trigger_kind": "schedule"},
    )


def _signal(
    signal_id: str = "signal-1", dedupe_key: str = "repo:changed"
) -> ProactiveSignalRecord:
    return ProactiveSignalRecord(
        signal_id=signal_id,
        dedupe_key=dedupe_key,
        session_id="session-1",
        topic_id="topic-1",
        kind="repo_activity",
        status="new",
        observed_at=_dt(9, 15),
        cooldown_until=None,
        summary="Repository activity signal",
        metadata={"signal_kind": "repo_activity"},
    )


def _signal_with_state(
    signal_id: str,
    *,
    status: str = "new",
    cooldown_until: datetime | None = None,
    session_id: str | None = "session-1",
) -> ProactiveSignalRecord:
    return ProactiveSignalRecord(
        signal_id=signal_id,
        dedupe_key=f"repo:{signal_id}",
        session_id=session_id,
        topic_id="topic-1",
        kind="repo_activity",
        status=status,
        observed_at=_dt(9, 15),
        cooldown_until=cooldown_until,
        summary="Repository activity signal",
        metadata={"signal_kind": "repo_activity"},
    )


def _intent(topic_id: str | None = "topic-1") -> ScheduledLaunchIntent:
    return ScheduledLaunchIntent(
        trigger_id="trigger-1",
        schedule_id="schedule-1",
        session_id="session-1",
        topic_id=topic_id,
        reason="schedule_due",
        due_at=_dt(10),
        planned_at=_dt(9, 30),
        metadata={"schedule_kind": "interval"},
    )


def _topic(topic_id: str = "topic-1", *, tape_id: str = "tape-1") -> TopicRecord:
    return TopicRecord(
        topic_id=topic_id,
        tape_id=tape_id,
        session_id="session-1",
        kind="coding",
        status="open",
        title="Existing topic",
        summary=None,
        owner="local",
        topic_initial_seq=0,
        topic_finalized_seq=None,
        created_at=_dt(8),
        finalized_at=None,
        metadata={"profile": "local"},
    )


def _closed_topic(
    topic_id: str = "topic-closed", *, tape_id: str = "tape-1"
) -> TopicRecord:
    return TopicRecord(
        topic_id=topic_id,
        tape_id=tape_id,
        session_id="session-1",
        kind="coding",
        status="finalized",
        title="Closed topic",
        summary="Closed safely",
        owner="local",
        topic_initial_seq=0,
        topic_finalized_seq=1,
        created_at=_dt(8),
        finalized_at=_dt(8, 30),
        metadata={"profile": "local"},
    )


def _preparer(topic_store: FakeScheduledTopicStore) -> ScheduledRunLaunchPreparer:
    ids = iter(("topic-created",))
    lifecycle = TopicLifecycle(
        store=topic_store,
        now=lambda: _dt(9),
        topic_id_factory=lambda: next(ids),
    )
    return ScheduledRunLaunchPreparer(
        topic_store=topic_store,
        topic_lifecycle=lifecycle,
    )


def _schedule_row(*args: object) -> dict[str, object]:
    (
        schedule_id,
        session_id,
        topic_id,
        kind,
        status,
        cadence,
        owner,
        title,
        next_due_at,
        last_triggered_at,
        created_at,
        updated_at,
        metadata,
    ) = args
    return {
        "schedule_id": schedule_id,
        "session_id": session_id,
        "topic_id": topic_id,
        "kind": kind,
        "status": status,
        "cadence": cadence,
        "owner": owner,
        "title": title,
        "next_due_at": next_due_at,
        "last_triggered_at": last_triggered_at,
        "created_at": created_at,
        "updated_at": updated_at,
        "metadata": metadata,
    }


def _trigger_row(*args: object) -> dict[str, object]:
    (
        trigger_id,
        schedule_id,
        signal_id,
        topic_id,
        run_id,
        status,
        due_at,
        planned_at,
        reason,
        metadata,
    ) = args
    return {
        "trigger_id": trigger_id,
        "schedule_id": schedule_id,
        "signal_id": signal_id,
        "topic_id": topic_id,
        "run_id": run_id,
        "status": status,
        "due_at": due_at,
        "planned_at": planned_at,
        "reason": reason,
        "metadata": metadata,
    }


def _signal_row(*args: object) -> dict[str, object]:
    (
        signal_id,
        dedupe_key,
        session_id,
        topic_id,
        kind,
        status,
        observed_at,
        cooldown_until,
        summary,
        metadata,
    ) = args
    return {
        "signal_id": signal_id,
        "dedupe_key": dedupe_key,
        "session_id": session_id,
        "topic_id": topic_id,
        "kind": kind,
        "status": status,
        "observed_at": observed_at,
        "cooldown_until": cooldown_until,
        "summary": summary,
        "metadata": metadata,
    }


@pytest.mark.asyncio
async def test_schedule_store_schema_is_idempotent(
    store: PGScheduledRunStore,
    fake_pool: FakeScheduledPool,
) -> None:
    await store.create_schedule(_schedule())
    await store.load_schedule("schedule-1")
    await store.record_signal(_signal())

    schema_calls = [
        query
        for query, _args in fake_pool.executed
        if "CREATE TABLE IF NOT EXISTS scheduled_runs" in query
    ]

    assert len(schema_calls) == 1
    assert "CREATE TABLE IF NOT EXISTS scheduled_run_triggers" in schema_calls[0]
    assert "CREATE TABLE IF NOT EXISTS proactive_signals" in schema_calls[0]
    assert "agent_runs" not in schema_calls[0]


@pytest.mark.asyncio
async def test_schedule_store_create_update_list_and_record_trigger(
    store: PGScheduledRunStore,
) -> None:
    created = await store.create_schedule(_schedule())
    await store.create_schedule(_schedule("schedule-2"))

    updated = await store.update_schedule_status(
        "schedule-1",
        status="paused",
        next_due_at=_dt(11),
        last_triggered_at=_dt(10),
        updated_at=_dt(10, 1),
        metadata={"reason": "manual_pause"},
    )
    trigger = await store.record_trigger(_trigger())

    active = await store.list_schedules(status="active")
    triggers = await store.list_triggers("schedule-1")

    assert created.schedule_id == "schedule-1"
    assert updated.status == "paused"
    assert updated.next_due_at == _dt(11)
    assert active == [_schedule("schedule-2")]
    assert trigger.reason == "due"
    assert triggers == [trigger]


@pytest.mark.asyncio
async def test_proactive_signal_store_deduplicates_signals(
    store: PGScheduledRunStore,
) -> None:
    first = await store.record_signal(_signal("signal-1", "repo:changed"))
    duplicate = await store.record_signal(_signal("signal-2", "repo:changed"))

    updated = await store.update_signal_status(
        "signal-1",
        status="planned",
        cooldown_until=_dt(10),
        metadata={"route": "schedule"},
    )
    listed = await store.list_signals(status="planned")

    assert duplicate.signal_id == first.signal_id
    assert updated.status == "planned"
    assert updated.cooldown_until == _dt(10)
    assert listed == [updated]


def test_schedule_records_reject_sensitive_metadata_and_summary() -> None:
    with pytest.raises(ValueError, match="forbidden metadata key"):
        ScheduleRecord(
            schedule_id="schedule-1",
            session_id="session-1",
            kind="interval",
            status="active",
            cadence="daily",
            created_at=_dt(9),
            updated_at=_dt(9),
            metadata={"prompt": "do not store"},
        )

    with pytest.raises(ValueError, match="secret-shaped"):
        ProactiveSignalRecord(
            signal_id="signal-1",
            dedupe_key="signal",
            kind="repo_activity",
            status="new",
            observed_at=_dt(9),
            summary="token=secret",
        )

    with pytest.raises(ValueError, match="sensitive label text"):
        ScheduleRecord(
            schedule_id="schedule-1",
            session_id="session-1",
            kind="interval",
            status="active",
            cadence="prompt: raw request",
            created_at=_dt(9),
            updated_at=_dt(9),
        )

    with pytest.raises(ValueError, match="sensitive label text"):
        ProactiveSignalRecord(
            signal_id="signal-1",
            dedupe_key="token=secret",
            kind="repo_activity",
            status="new",
            observed_at=_dt(9),
            summary="Repository activity signal",
        )


@pytest.mark.asyncio
async def test_schedule_planner_returns_bounded_due_launch_intents(
    store: PGScheduledRunStore,
) -> None:
    await store.create_schedule(_schedule_with_due("schedule-1", next_due_at=_dt(8)))
    await store.create_schedule(_schedule_with_due("schedule-2", next_due_at=_dt(8)))
    await store.create_schedule(_schedule_with_due("schedule-3", next_due_at=_dt(12)))

    planner = ScheduledRunPlanner(
        store=store,
        trigger_id_factory=lambda schedule, _now: f"trigger-{schedule.schedule_id}",
    )

    intents = await planner.plan_due_schedules(now=_dt(10), max_due=1)

    assert [intent.schedule_id for intent in intents] == ["schedule-1"]
    assert intents[0].trigger_id == "trigger-schedule-1"
    assert intents[0].session_id == "session-1"
    assert intents[0].topic_id == "topic-1"
    assert intents[0].reason == "schedule_due"
    assert await store.list_triggers("schedule-1") == [
        ScheduleTriggerRecord(
            trigger_id="trigger-schedule-1",
            schedule_id="schedule-1",
            signal_id=None,
            topic_id="topic-1",
            run_id=None,
            status="planned",
            due_at=_dt(8),
            planned_at=_dt(10),
            reason="schedule_due",
            metadata={"trigger_kind": "schedule", "schedule_kind": "interval"},
        )
    ]
    assert (await store.load_schedule("schedule-1")).next_due_at == _dt(10) + timedelta(
        days=1
    )


@pytest.mark.asyncio
async def test_schedule_planner_skips_inactive_and_not_due_without_execution(
    store: PGScheduledRunStore,
) -> None:
    await store.create_schedule(
        _schedule_with_due("schedule-paused", next_due_at=_dt(8), status="paused")
    )
    await store.create_schedule(
        _schedule_with_due("schedule-future", next_due_at=_dt(12))
    )

    planner = ScheduledRunPlanner(store=store)

    intents = await planner.plan_due_schedules(now=_dt(10), max_due=5)

    assert intents == []
    assert await store.list_triggers("schedule-paused") == []
    assert await store.list_triggers("schedule-future") == []


@pytest.mark.asyncio
async def test_schedule_planner_does_not_starve_due_rows_behind_null_due(
    store: PGScheduledRunStore,
) -> None:
    await store.create_schedule(_schedule_with_due("schedule-null", next_due_at=None))
    await store.create_schedule(_schedule_with_due("schedule-due", next_due_at=_dt(8)))

    planner = ScheduledRunPlanner(
        store=store,
        trigger_id_factory=lambda schedule, _now: f"trigger-{schedule.schedule_id}",
    )

    intents = await planner.plan_due_schedules(now=_dt(10), max_due=1)

    assert [intent.schedule_id for intent in intents] == ["schedule-due"]
    assert await store.list_triggers("schedule-null") == []
    assert [
        trigger.schedule_id for trigger in await store.list_triggers("schedule-due")
    ] == ["schedule-due"]


@pytest.mark.asyncio
async def test_topic_aware_launch_continues_open_topic() -> None:
    topic_store = FakeScheduledTopicStore()
    topic_store.topics["topic-1"] = _topic()
    tape = Tape(tape_id="tape-1")

    prepared = await _preparer(topic_store).prepare(intent=_intent(), tape=tape)

    assert prepared.topic_id == "topic-1"
    assert prepared.run_metadata["scheduled_run"] == {
        "schedule_id": "schedule-1",
        "trigger_id": "trigger-1",
        "reason": "schedule_due",
        "planned_at": _dt(9, 30).isoformat(),
    }
    assert prepared.run_metadata["topic"] == {
        "topic_id": "topic-1",
        "tape_id": "tape-1",
        "session_id": "session-1",
        "kind": "coding",
        "status": "open",
        "topic_initial_seq": 0,
        "topic_finalized_seq": None,
    }
    assert topic_store.anchors == []


@pytest.mark.asyncio
async def test_topic_aware_launch_creates_topic_when_missing() -> None:
    topic_store = FakeScheduledTopicStore()
    tape = Tape(tape_id="tape-1")

    prepared = await _preparer(topic_store).prepare(intent=_intent(None), tape=tape)

    assert prepared.topic_id == "topic-created"
    assert prepared.run_metadata["topic"]["topic_id"] == "topic-created"
    assert topic_store.topics["topic-created"].metadata == {
        "source": "scheduled_run",
        "schedule_kind": "interval",
    }
    assert topic_store.anchors[0].topic_id == "topic-created"
    assert len(tape) == 1


@pytest.mark.asyncio
async def test_topic_aware_launch_rejects_topic_tape_mismatch() -> None:
    topic_store = FakeScheduledTopicStore()
    topic_store.topics["topic-1"] = _topic(tape_id="other-tape")
    tape = Tape(tape_id="tape-1")

    with pytest.raises(ValueError, match="tape does not match"):
        await _preparer(topic_store).prepare(intent=_intent(), tape=tape)


@pytest.mark.asyncio
async def test_topic_aware_launch_rejects_closed_explicit_topic() -> None:
    topic_store = FakeScheduledTopicStore()
    topic_store.topics["topic-closed"] = _closed_topic("topic-closed")
    topic_store.topics["topic-open"] = _topic("topic-open")
    tape = Tape(tape_id="tape-1")

    with pytest.raises(ValueError, match="scheduled topic is not open"):
        await _preparer(topic_store).prepare(
            intent=_intent("topic-closed"),
            tape=tape,
        )

    assert "topic-created" not in topic_store.topics


@pytest.mark.asyncio
async def test_topic_aware_launch_rejects_missing_explicit_topic() -> None:
    topic_store = FakeScheduledTopicStore()
    tape = Tape(tape_id="tape-1")

    with pytest.raises(ValueError, match="scheduled topic is not open"):
        await _preparer(topic_store).prepare(
            intent=_intent("topic-missing"),
            tape=tape,
        )

    assert topic_store.topics == {}


@pytest.mark.asyncio
async def test_proactive_signal_planner_returns_bounded_launch_intents(
    store: PGScheduledRunStore,
) -> None:
    await store.record_signal(_signal_with_state("signal-1"))
    await store.record_signal(_signal_with_state("signal-2"))

    planner = ProactiveSignalPlanner(store=store, cooldown=timedelta(minutes=15))

    intents = await planner.plan_new_signals(now=_dt(10), max_signals=1)

    assert [intent.schedule_id for intent in intents] == ["signal:signal-1"]
    assert intents[0].trigger_id == "signal-trigger-signal-1"
    assert intents[0].reason == "proactive_signal"
    assert intents[0].session_id == "session-1"
    assert intents[0].topic_id == "topic-1"
    assert await store.list_triggers("signal:signal-1") == [
        ScheduleTriggerRecord(
            trigger_id="signal-trigger-signal-1",
            schedule_id="signal:signal-1",
            signal_id="signal-1",
            topic_id="topic-1",
            run_id=None,
            status="planned",
            due_at=_dt(9, 15),
            planned_at=_dt(10),
            reason="proactive_signal",
            metadata={
                "trigger_kind": "proactive_signal",
                "signal_kind": "repo_activity",
            },
        )
    ]
    assert (await store.load_signal("signal-1")).status == "planned"
    assert (await store.load_signal("signal-1")).cooldown_until == _dt(10, 15)
    assert (await store.load_signal("signal-2")).status == "new"


@pytest.mark.asyncio
async def test_proactive_signal_planner_skips_cooldown_without_looping(
    store: PGScheduledRunStore,
) -> None:
    await store.record_signal(
        _signal_with_state("signal-cooldown", cooldown_until=_dt(11))
    )

    planner = ProactiveSignalPlanner(store=store, cooldown=timedelta(minutes=15))

    intents = await planner.plan_new_signals(now=_dt(10), max_signals=5)

    signal = await store.load_signal("signal-cooldown")
    assert intents == []
    assert signal.status == "ignored"
    assert signal.cooldown_until == _dt(11)
    assert signal.metadata["skip_reason"] == "cooldown"


@pytest.mark.asyncio
async def test_proactive_signal_planner_requires_session_for_launch(
    store: PGScheduledRunStore,
) -> None:
    await store.record_signal(_signal_with_state("signal-no-session", session_id=None))
    planner = ProactiveSignalPlanner(store=store, cooldown=timedelta(minutes=15))

    with pytest.raises(ValueError, match="requires session_id"):
        await planner.plan_new_signals(now=_dt(10), max_signals=1)

    assert (await store.load_signal("signal-no-session")).status == "new"
    assert await store.list_triggers("signal:signal-no-session") == []
