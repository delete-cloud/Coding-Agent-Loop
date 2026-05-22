"""Durable scheduled run and proactive signal records."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final, Protocol

from agentkit.storage.pg import AsyncPGPool, PGPool
from agentkit.tape.tape import Tape
from coding_agent.topic_lifecycle import TopicLifecycle
from coding_agent.topic_store import JSONObject
from coding_agent.topic_store import TopicRecord

ScheduleStatus = str
SignalStatus = str
TriggerStatus = str

_SCHEDULE_STATUSES: Final[frozenset[str]] = frozenset(
    {"active", "paused", "disabled", "completed"}
)
_SIGNAL_STATUSES: Final[frozenset[str]] = frozenset(
    {"new", "planned", "ignored", "consumed"}
)
_TRIGGER_STATUSES: Final[frozenset[str]] = frozenset(
    {"planned", "launched", "skipped", "failed"}
)
_MAX_DISPLAY_TEXT_CHARS: Final[int] = 256
_MAX_METADATA_STRING_CHARS: Final[int] = 256
_MAX_SAFE_LABEL_CHARS: Final[int] = 128
_FORBIDDEN_METADATA_KEY_PARTS: Final[frozenset[str]] = frozenset(
    {
        "command_output",
        "content",
        "env",
        "message",
        "prompt",
        "result",
        "secret",
        "stderr",
        "stdout",
        "text",
    }
)
_SECRET_VALUE_MARKERS: Final[tuple[str, ...]] = (
    "-----begin ",
    "akia",
    "password=",
    "secret=",
    "sk-",
    "token=",
)


@dataclass(frozen=True)
class ScheduleRecord:
    schedule_id: str
    session_id: str
    kind: str
    status: ScheduleStatus
    cadence: str
    created_at: datetime
    updated_at: datetime
    topic_id: str | None = None
    owner: str | None = None
    title: str | None = None
    next_due_at: datetime | None = None
    last_triggered_at: datetime | None = None
    metadata: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("schedule_id", self.schedule_id)
        _require_non_empty("session_id", self.session_id)
        _require_safe_label("kind", self.kind)
        _require_safe_label("cadence", self.cadence)
        _require_status("schedule status", self.status, _SCHEDULE_STATUSES)
        _require_datetime("created_at", self.created_at)
        _require_datetime("updated_at", self.updated_at)
        if self.topic_id is not None:
            _require_non_empty("topic_id", self.topic_id)
        _require_optional_display_text("owner", self.owner)
        _require_optional_display_text("title", self.title)
        if self.next_due_at is not None:
            _require_datetime("next_due_at", self.next_due_at)
        if self.last_triggered_at is not None:
            _require_datetime("last_triggered_at", self.last_triggered_at)
        _require_json_object("metadata", self.metadata)


@dataclass(frozen=True)
class ScheduleTriggerRecord:
    trigger_id: str
    schedule_id: str
    status: TriggerStatus
    due_at: datetime
    planned_at: datetime
    topic_id: str | None = None
    signal_id: str | None = None
    run_id: str | None = None
    reason: str | None = None
    metadata: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("trigger_id", self.trigger_id)
        _require_non_empty("schedule_id", self.schedule_id)
        _require_status("trigger status", self.status, _TRIGGER_STATUSES)
        _require_datetime("due_at", self.due_at)
        _require_datetime("planned_at", self.planned_at)
        if self.topic_id is not None:
            _require_non_empty("topic_id", self.topic_id)
        if self.signal_id is not None:
            _require_non_empty("signal_id", self.signal_id)
        if self.run_id is not None:
            _require_non_empty("run_id", self.run_id)
        _require_optional_display_text("reason", self.reason)
        _require_json_object("metadata", self.metadata)


@dataclass(frozen=True)
class ProactiveSignalRecord:
    signal_id: str
    dedupe_key: str
    kind: str
    status: SignalStatus
    observed_at: datetime
    summary: str
    session_id: str | None = None
    topic_id: str | None = None
    cooldown_until: datetime | None = None
    metadata: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("signal_id", self.signal_id)
        _require_safe_label("dedupe_key", self.dedupe_key)
        _require_safe_label("kind", self.kind)
        _require_status("signal status", self.status, _SIGNAL_STATUSES)
        _require_datetime("observed_at", self.observed_at)
        _require_optional_display_text("summary", self.summary)
        if self.session_id is not None:
            _require_non_empty("session_id", self.session_id)
        if self.topic_id is not None:
            _require_non_empty("topic_id", self.topic_id)
        if self.cooldown_until is not None:
            _require_datetime("cooldown_until", self.cooldown_until)
        _require_json_object("metadata", self.metadata)


@dataclass(frozen=True)
class ScheduledLaunchIntent:
    trigger_id: str
    schedule_id: str
    session_id: str
    topic_id: str | None
    reason: str
    due_at: datetime
    planned_at: datetime
    metadata: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("trigger_id", self.trigger_id)
        _require_non_empty("schedule_id", self.schedule_id)
        _require_non_empty("session_id", self.session_id)
        if self.topic_id is not None:
            _require_non_empty("topic_id", self.topic_id)
        _require_safe_label("reason", self.reason)
        _require_datetime("due_at", self.due_at)
        _require_datetime("planned_at", self.planned_at)
        _require_json_object("metadata", self.metadata)


@dataclass(frozen=True)
class PreparedScheduledRun:
    trigger_id: str
    schedule_id: str
    session_id: str
    topic_id: str
    run_metadata: JSONObject

    def __post_init__(self) -> None:
        _require_non_empty("trigger_id", self.trigger_id)
        _require_non_empty("schedule_id", self.schedule_id)
        _require_non_empty("session_id", self.session_id)
        _require_non_empty("topic_id", self.topic_id)
        _require_json_object("run_metadata", self.run_metadata)


class ScheduledRunPlannerStore(Protocol):
    async def list_due_schedules(
        self,
        *,
        due_at_or_before: datetime,
        limit: int,
    ) -> list[ScheduleRecord]: ...

    async def update_schedule_status(
        self,
        schedule_id: str,
        *,
        status: ScheduleStatus,
        next_due_at: datetime | None,
        last_triggered_at: datetime | None,
        updated_at: datetime,
        metadata: JSONObject,
    ) -> ScheduleRecord: ...

    async def record_trigger(
        self,
        record: ScheduleTriggerRecord,
    ) -> ScheduleTriggerRecord: ...


class ProactiveSignalPlannerStore(Protocol):
    async def list_signals(
        self,
        *,
        status: SignalStatus | None = None,
        session_id: str | None = None,
        topic_id: str | None = None,
        limit: int = 100,
    ) -> list[ProactiveSignalRecord]: ...

    async def update_signal_status(
        self,
        signal_id: str,
        *,
        status: SignalStatus,
        cooldown_until: datetime | None,
        metadata: JSONObject,
    ) -> ProactiveSignalRecord: ...

    async def record_trigger(
        self,
        record: ScheduleTriggerRecord,
    ) -> ScheduleTriggerRecord: ...


class ScheduledRunPlanner:
    def __init__(
        self,
        *,
        store: ScheduledRunPlannerStore,
        trigger_id_factory: Callable[[ScheduleRecord, datetime], str] | None = None,
    ) -> None:
        self._store = store
        self._trigger_id_factory = trigger_id_factory or _default_trigger_id

    async def plan_due_schedules(
        self,
        *,
        now: datetime,
        max_due: int,
    ) -> list[ScheduledLaunchIntent]:
        _require_datetime("now", now)
        _require_positive_int("max_due", max_due)
        schedules = await self._store.list_due_schedules(
            due_at_or_before=now,
            limit=max_due,
        )
        intents: list[ScheduledLaunchIntent] = []
        for schedule in schedules:
            if len(intents) >= max_due:
                break
            if schedule.next_due_at is None or schedule.next_due_at > now:
                continue
            trigger_id = self._trigger_id_factory(schedule, now)
            trigger = await self._store.record_trigger(
                ScheduleTriggerRecord(
                    trigger_id=trigger_id,
                    schedule_id=schedule.schedule_id,
                    topic_id=schedule.topic_id,
                    status="planned",
                    due_at=schedule.next_due_at,
                    planned_at=now,
                    reason="schedule_due",
                    metadata={
                        "trigger_kind": "schedule",
                        "schedule_kind": schedule.kind,
                        **_schedule_bee_launch_metadata(schedule),
                    },
                )
            )
            next_due_at = _next_due_at(schedule, now)
            await self._store.update_schedule_status(
                schedule.schedule_id,
                status="active",
                next_due_at=next_due_at,
                last_triggered_at=now,
                updated_at=now,
                metadata={
                    **schedule.metadata,
                    "last_trigger_status": "planned",
                },
            )
            intents.append(
                ScheduledLaunchIntent(
                    trigger_id=trigger.trigger_id,
                    schedule_id=schedule.schedule_id,
                    session_id=schedule.session_id,
                    topic_id=schedule.topic_id,
                    reason="schedule_due",
                    due_at=trigger.due_at,
                    planned_at=trigger.planned_at,
                    metadata={
                        "schedule_kind": schedule.kind,
                        **_schedule_bee_launch_metadata(schedule),
                    },
                )
            )
        return intents


class ProactiveSignalPlanner:
    def __init__(
        self,
        *,
        store: ProactiveSignalPlannerStore,
        cooldown: timedelta = timedelta(minutes=30),
    ) -> None:
        if cooldown.total_seconds() <= 0:
            raise ValueError("cooldown must be positive")
        self._store = store
        self._cooldown = cooldown

    async def plan_new_signals(
        self,
        *,
        now: datetime,
        max_signals: int,
    ) -> list[ScheduledLaunchIntent]:
        _require_datetime("now", now)
        _require_positive_int("max_signals", max_signals)
        signals = await self._store.list_signals(status="new", limit=max_signals)
        intents: list[ScheduledLaunchIntent] = []
        for signal in signals:
            if len(intents) >= max_signals:
                break
            if signal.cooldown_until is not None and signal.cooldown_until > now:
                await self._store.update_signal_status(
                    signal.signal_id,
                    status="ignored",
                    cooldown_until=signal.cooldown_until,
                    metadata={
                        **signal.metadata,
                        "skip_reason": "cooldown",
                    },
                )
                continue
            session_id = _required_signal_session(signal)
            cooldown_until = now + self._cooldown
            updated = await self._store.update_signal_status(
                signal.signal_id,
                status="planned",
                cooldown_until=cooldown_until,
                metadata={
                    **signal.metadata,
                    "planned_reason": "proactive_signal",
                },
            )
            trigger = await self._store.record_trigger(
                ScheduleTriggerRecord(
                    trigger_id=f"signal-trigger-{updated.signal_id}",
                    schedule_id=f"signal:{updated.signal_id}",
                    signal_id=updated.signal_id,
                    topic_id=updated.topic_id,
                    status="planned",
                    due_at=updated.observed_at,
                    planned_at=now,
                    reason="proactive_signal",
                    metadata={
                        "trigger_kind": "proactive_signal",
                        "signal_kind": updated.kind,
                    },
                )
            )
            intents.append(
                ScheduledLaunchIntent(
                    trigger_id=trigger.trigger_id,
                    schedule_id=trigger.schedule_id,
                    session_id=session_id,
                    topic_id=updated.topic_id,
                    reason="proactive_signal",
                    due_at=trigger.due_at,
                    planned_at=trigger.planned_at,
                    metadata={"signal_kind": updated.kind},
                )
            )
        return intents


class ScheduledTopicStore(Protocol):
    async def load_topic(self, topic_id: str) -> TopicRecord | None: ...

    async def find_open_topic(
        self,
        *,
        session_id: str,
        tape_id: str,
    ) -> TopicRecord | None: ...


class ScheduledRunLaunchPreparer:
    def __init__(
        self,
        *,
        topic_store: ScheduledTopicStore,
        topic_lifecycle: TopicLifecycle,
    ) -> None:
        self._topic_store = topic_store
        self._topic_lifecycle = topic_lifecycle

    async def prepare(
        self,
        *,
        intent: ScheduledLaunchIntent,
        tape: Tape,
    ) -> PreparedScheduledRun:
        topic = await self._resolve_topic(intent=intent, tape=tape)
        metadata: JSONObject = {
            "scheduled_run": {
                "schedule_id": intent.schedule_id,
                "trigger_id": intent.trigger_id,
                "reason": intent.reason,
                "planned_at": intent.planned_at.isoformat(),
            },
            "topic": {
                "topic_id": topic.topic_id,
                "tape_id": topic.tape_id,
                "session_id": topic.session_id,
                "kind": topic.kind,
                "status": topic.status,
                "topic_initial_seq": topic.topic_initial_seq,
                "topic_finalized_seq": topic.topic_finalized_seq,
            },
        }
        return PreparedScheduledRun(
            trigger_id=intent.trigger_id,
            schedule_id=intent.schedule_id,
            session_id=intent.session_id,
            topic_id=topic.topic_id,
            run_metadata=metadata,
        )

    async def _resolve_topic(
        self,
        *,
        intent: ScheduledLaunchIntent,
        tape: Tape,
    ) -> TopicRecord:
        if intent.topic_id is not None:
            topic = await self._topic_store.load_topic(intent.topic_id)
            if topic is not None and topic.status == "open":
                _require_topic_matches_intent(topic=topic, intent=intent, tape=tape)
                return topic
            raise ValueError("scheduled topic is not open")
        open_topic = await self._topic_store.find_open_topic(
            session_id=intent.session_id,
            tape_id=tape.tape_id,
        )
        if open_topic is not None:
            return open_topic
        return await self._create_scheduled_topic(intent=intent, tape=tape)

    async def _create_scheduled_topic(
        self,
        *,
        intent: ScheduledLaunchIntent,
        tape: Tape,
    ) -> TopicRecord:
        metadata: JSONObject = {
            "source": "scheduled_run",
            "schedule_kind": _safe_metadata_label(intent.metadata.get("schedule_kind")),
        }
        return await self._topic_lifecycle.create_topic(
            tape=tape,
            session_id=intent.session_id,
            kind="coding",
            title="Scheduled run",
            owner=None,
            metadata=metadata,
        )


class PGScheduledRunStore:
    _CREATE_SCHEMA_SQL: Final[str] = """
    CREATE TABLE IF NOT EXISTS scheduled_runs (
        schedule_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        topic_id TEXT,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        cadence TEXT NOT NULL,
        owner TEXT,
        title TEXT,
        next_due_at TIMESTAMPTZ,
        last_triggered_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb
    );

    CREATE INDEX IF NOT EXISTS scheduled_runs_session_status_due_idx
        ON scheduled_runs (session_id, status, next_due_at, schedule_id);

    CREATE INDEX IF NOT EXISTS scheduled_runs_topic_status_due_idx
        ON scheduled_runs (topic_id, status, next_due_at, schedule_id);

    CREATE TABLE IF NOT EXISTS scheduled_run_triggers (
        trigger_id TEXT PRIMARY KEY,
        schedule_id TEXT NOT NULL,
        signal_id TEXT,
        topic_id TEXT,
        run_id TEXT,
        status TEXT NOT NULL,
        due_at TIMESTAMPTZ NOT NULL,
        planned_at TIMESTAMPTZ NOT NULL,
        reason TEXT,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb
    );

    CREATE INDEX IF NOT EXISTS scheduled_run_triggers_schedule_due_idx
        ON scheduled_run_triggers (schedule_id, due_at, trigger_id);

    CREATE TABLE IF NOT EXISTS proactive_signals (
        signal_id TEXT PRIMARY KEY,
        dedupe_key TEXT NOT NULL UNIQUE,
        session_id TEXT,
        topic_id TEXT,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        observed_at TIMESTAMPTZ NOT NULL,
        cooldown_until TIMESTAMPTZ,
        summary TEXT NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb
    );

    CREATE INDEX IF NOT EXISTS proactive_signals_status_observed_idx
        ON proactive_signals (status, observed_at, signal_id);
    """
    _UPSERT_SCHEDULE_SQL: Final[str] = """
    INSERT INTO scheduled_runs (
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
        metadata
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb)
    ON CONFLICT (schedule_id)
    DO UPDATE SET
        session_id = EXCLUDED.session_id,
        topic_id = EXCLUDED.topic_id,
        kind = EXCLUDED.kind,
        status = EXCLUDED.status,
        cadence = EXCLUDED.cadence,
        owner = EXCLUDED.owner,
        title = EXCLUDED.title,
        next_due_at = EXCLUDED.next_due_at,
        last_triggered_at = EXCLUDED.last_triggered_at,
        updated_at = EXCLUDED.updated_at,
        metadata = EXCLUDED.metadata
    RETURNING *
    """
    _SELECT_SCHEDULE_SQL: Final[str] = """
    SELECT * FROM scheduled_runs WHERE schedule_id = $1
    """
    _LIST_SCHEDULES_SQL: Final[str] = """
    SELECT * FROM scheduled_runs
    WHERE ($1::text IS NULL OR session_id = $1)
      AND ($2::text IS NULL OR topic_id = $2)
      AND ($3::text IS NULL OR status = $3)
    ORDER BY COALESCE(next_due_at, updated_at), schedule_id
    LIMIT $4
    """
    _LIST_DUE_SCHEDULES_SQL: Final[str] = """
    SELECT * FROM scheduled_runs
    WHERE status = 'active'
      AND next_due_at IS NOT NULL
      AND next_due_at <= $1
    ORDER BY next_due_at, schedule_id
    LIMIT $2
    """
    _UPDATE_SCHEDULE_STATUS_SQL: Final[str] = """
    UPDATE scheduled_runs
    SET status = $2,
        next_due_at = $3,
        last_triggered_at = $4,
        updated_at = $5,
        metadata = $6::jsonb
    WHERE schedule_id = $1
    RETURNING *
    """
    _INSERT_TRIGGER_SQL: Final[str] = """
    INSERT INTO scheduled_run_triggers (
        trigger_id,
        schedule_id,
        signal_id,
        topic_id,
        run_id,
        status,
        due_at,
        planned_at,
        reason,
        metadata
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
    ON CONFLICT (trigger_id)
    DO UPDATE SET
        schedule_id = EXCLUDED.schedule_id,
        signal_id = EXCLUDED.signal_id,
        topic_id = EXCLUDED.topic_id,
        run_id = EXCLUDED.run_id,
        status = EXCLUDED.status,
        due_at = EXCLUDED.due_at,
        planned_at = EXCLUDED.planned_at,
        reason = EXCLUDED.reason,
        metadata = EXCLUDED.metadata
    RETURNING *
    """
    _LIST_TRIGGERS_SQL: Final[str] = """
    SELECT * FROM scheduled_run_triggers
    WHERE schedule_id = $1
    ORDER BY due_at, trigger_id
    LIMIT $2
    """
    _UPSERT_SIGNAL_SQL: Final[str] = """
    INSERT INTO proactive_signals (
        signal_id,
        dedupe_key,
        session_id,
        topic_id,
        kind,
        status,
        observed_at,
        cooldown_until,
        summary,
        metadata
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
    ON CONFLICT (dedupe_key)
    DO UPDATE SET
        status = proactive_signals.status,
        metadata = proactive_signals.metadata
    RETURNING *
    """
    _SELECT_SIGNAL_SQL: Final[str] = """
    SELECT * FROM proactive_signals WHERE signal_id = $1
    """
    _LIST_SIGNALS_SQL: Final[str] = """
    SELECT * FROM proactive_signals
    WHERE ($1::text IS NULL OR status = $1)
      AND ($2::text IS NULL OR session_id = $2)
      AND ($3::text IS NULL OR topic_id = $3)
    ORDER BY observed_at, signal_id
    LIMIT $4
    """
    _UPDATE_SIGNAL_STATUS_SQL: Final[str] = """
    UPDATE proactive_signals
    SET status = $2,
        cooldown_until = $3,
        metadata = $4::jsonb
    WHERE signal_id = $1
    RETURNING *
    """

    def __init__(self, *, pool: PGPool) -> None:
        self._pool = pool
        self._schema_ready = False

    async def _ensure_schema(self) -> AsyncPGPool:
        pool = await self._pool.get_pool()
        if not self._schema_ready:
            _ = await pool.execute(self._CREATE_SCHEMA_SQL)
            self._schema_ready = True
        return pool

    async def create_schedule(self, record: ScheduleRecord) -> ScheduleRecord:
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._UPSERT_SCHEDULE_SQL,
            record.schedule_id,
            record.session_id,
            record.topic_id,
            record.kind,
            record.status,
            record.cadence,
            record.owner,
            record.title,
            record.next_due_at,
            record.last_triggered_at,
            record.created_at,
            record.updated_at,
            record.metadata,
        )
        return _schedule_from_row(_required_row(row, "schedule upsert"))

    async def load_schedule(self, schedule_id: str) -> ScheduleRecord | None:
        _require_non_empty("schedule_id", schedule_id)
        pool = await self._ensure_schema()
        row = await pool.fetchrow(self._SELECT_SCHEDULE_SQL, schedule_id)
        return None if row is None else _schedule_from_row(row)

    async def list_schedules(
        self,
        *,
        session_id: str | None = None,
        topic_id: str | None = None,
        status: ScheduleStatus | None = None,
        limit: int = 100,
    ) -> list[ScheduleRecord]:
        if session_id is not None:
            _require_non_empty("session_id", session_id)
        if topic_id is not None:
            _require_non_empty("topic_id", topic_id)
        if status is not None:
            _require_status("schedule status", status, _SCHEDULE_STATUSES)
        _require_positive_int("limit", limit)
        pool = await self._ensure_schema()
        rows = await pool.fetch(
            self._LIST_SCHEDULES_SQL, session_id, topic_id, status, limit
        )
        return [_schedule_from_row(row) for row in rows]

    async def list_due_schedules(
        self,
        *,
        due_at_or_before: datetime,
        limit: int = 100,
    ) -> list[ScheduleRecord]:
        _require_datetime("due_at_or_before", due_at_or_before)
        _require_positive_int("limit", limit)
        pool = await self._ensure_schema()
        rows = await pool.fetch(self._LIST_DUE_SCHEDULES_SQL, due_at_or_before, limit)
        return [_schedule_from_row(row) for row in rows]

    async def update_schedule_status(
        self,
        schedule_id: str,
        *,
        status: ScheduleStatus,
        next_due_at: datetime | None,
        last_triggered_at: datetime | None,
        updated_at: datetime,
        metadata: JSONObject,
    ) -> ScheduleRecord:
        _require_non_empty("schedule_id", schedule_id)
        _require_status("schedule status", status, _SCHEDULE_STATUSES)
        if next_due_at is not None:
            _require_datetime("next_due_at", next_due_at)
        if last_triggered_at is not None:
            _require_datetime("last_triggered_at", last_triggered_at)
        _require_datetime("updated_at", updated_at)
        _require_json_object("metadata", metadata)
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._UPDATE_SCHEDULE_STATUS_SQL,
            schedule_id,
            status,
            next_due_at,
            last_triggered_at,
            updated_at,
            metadata,
        )
        if row is None:
            raise KeyError(f"schedule not found: {schedule_id}")
        return _schedule_from_row(row)

    async def record_trigger(
        self,
        record: ScheduleTriggerRecord,
    ) -> ScheduleTriggerRecord:
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._INSERT_TRIGGER_SQL,
            record.trigger_id,
            record.schedule_id,
            record.signal_id,
            record.topic_id,
            record.run_id,
            record.status,
            record.due_at,
            record.planned_at,
            record.reason,
            record.metadata,
        )
        return _trigger_from_row(_required_row(row, "schedule trigger upsert"))

    async def list_triggers(
        self,
        schedule_id: str,
        *,
        limit: int = 100,
    ) -> list[ScheduleTriggerRecord]:
        _require_non_empty("schedule_id", schedule_id)
        _require_positive_int("limit", limit)
        pool = await self._ensure_schema()
        rows = await pool.fetch(self._LIST_TRIGGERS_SQL, schedule_id, limit)
        return [_trigger_from_row(row) for row in rows]

    async def record_signal(
        self,
        record: ProactiveSignalRecord,
    ) -> ProactiveSignalRecord:
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._UPSERT_SIGNAL_SQL,
            record.signal_id,
            record.dedupe_key,
            record.session_id,
            record.topic_id,
            record.kind,
            record.status,
            record.observed_at,
            record.cooldown_until,
            record.summary,
            record.metadata,
        )
        return _signal_from_row(_required_row(row, "proactive signal upsert"))

    async def load_signal(self, signal_id: str) -> ProactiveSignalRecord | None:
        _require_non_empty("signal_id", signal_id)
        pool = await self._ensure_schema()
        row = await pool.fetchrow(self._SELECT_SIGNAL_SQL, signal_id)
        return None if row is None else _signal_from_row(row)

    async def list_signals(
        self,
        *,
        status: SignalStatus | None = None,
        session_id: str | None = None,
        topic_id: str | None = None,
        limit: int = 100,
    ) -> list[ProactiveSignalRecord]:
        if status is not None:
            _require_status("signal status", status, _SIGNAL_STATUSES)
        if session_id is not None:
            _require_non_empty("session_id", session_id)
        if topic_id is not None:
            _require_non_empty("topic_id", topic_id)
        _require_positive_int("limit", limit)
        pool = await self._ensure_schema()
        rows = await pool.fetch(
            self._LIST_SIGNALS_SQL, status, session_id, topic_id, limit
        )
        return [_signal_from_row(row) for row in rows]

    async def update_signal_status(
        self,
        signal_id: str,
        *,
        status: SignalStatus,
        cooldown_until: datetime | None,
        metadata: JSONObject,
    ) -> ProactiveSignalRecord:
        _require_non_empty("signal_id", signal_id)
        _require_status("signal status", status, _SIGNAL_STATUSES)
        if cooldown_until is not None:
            _require_datetime("cooldown_until", cooldown_until)
        _require_json_object("metadata", metadata)
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._UPDATE_SIGNAL_STATUS_SQL,
            signal_id,
            status,
            cooldown_until,
            metadata,
        )
        if row is None:
            raise KeyError(f"proactive signal not found: {signal_id}")
        return _signal_from_row(row)


def _required_row(row: dict[str, object] | None, context: str) -> dict[str, object]:
    if row is None:
        raise RuntimeError(f"postgres {context} returned no row")
    return row


def _schedule_from_row(row: dict[str, object]) -> ScheduleRecord:
    return ScheduleRecord(
        schedule_id=_required_str(row, "schedule_id", context="schedule row"),
        session_id=_required_str(row, "session_id", context="schedule row"),
        topic_id=_optional_str(row, "topic_id", context="schedule row"),
        kind=_required_str(row, "kind", context="schedule row"),
        status=_required_str(row, "status", context="schedule row"),
        cadence=_required_str(row, "cadence", context="schedule row"),
        owner=_optional_str(row, "owner", context="schedule row"),
        title=_optional_str(row, "title", context="schedule row"),
        next_due_at=_optional_datetime(row, "next_due_at", context="schedule row"),
        last_triggered_at=_optional_datetime(
            row, "last_triggered_at", context="schedule row"
        ),
        created_at=_required_datetime(row, "created_at", context="schedule row"),
        updated_at=_required_datetime(row, "updated_at", context="schedule row"),
        metadata=_required_json_object(row, "metadata", context="schedule row"),
    )


def _trigger_from_row(row: dict[str, object]) -> ScheduleTriggerRecord:
    return ScheduleTriggerRecord(
        trigger_id=_required_str(row, "trigger_id", context="trigger row"),
        schedule_id=_required_str(row, "schedule_id", context="trigger row"),
        signal_id=_optional_str(row, "signal_id", context="trigger row"),
        topic_id=_optional_str(row, "topic_id", context="trigger row"),
        run_id=_optional_str(row, "run_id", context="trigger row"),
        status=_required_str(row, "status", context="trigger row"),
        due_at=_required_datetime(row, "due_at", context="trigger row"),
        planned_at=_required_datetime(row, "planned_at", context="trigger row"),
        reason=_optional_str(row, "reason", context="trigger row"),
        metadata=_required_json_object(row, "metadata", context="trigger row"),
    )


def _signal_from_row(row: dict[str, object]) -> ProactiveSignalRecord:
    return ProactiveSignalRecord(
        signal_id=_required_str(row, "signal_id", context="signal row"),
        dedupe_key=_required_str(row, "dedupe_key", context="signal row"),
        session_id=_optional_str(row, "session_id", context="signal row"),
        topic_id=_optional_str(row, "topic_id", context="signal row"),
        kind=_required_str(row, "kind", context="signal row"),
        status=_required_str(row, "status", context="signal row"),
        observed_at=_required_datetime(row, "observed_at", context="signal row"),
        cooldown_until=_optional_datetime(row, "cooldown_until", context="signal row"),
        summary=_required_str(row, "summary", context="signal row"),
        metadata=_required_json_object(row, "metadata", context="signal row"),
    )


def _require_non_empty(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_safe_label(field_name: str, value: str) -> None:
    _require_non_empty(field_name, value)
    if len(value) > _MAX_SAFE_LABEL_CHARS:
        raise ValueError(
            f"{field_name} must be at most {_MAX_SAFE_LABEL_CHARS} characters"
        )
    key_folded = field_name.casefold()
    value_folded = value.casefold()
    if any(part in value_folded for part in _FORBIDDEN_METADATA_KEY_PARTS):
        raise ValueError(f"{field_name} must not contain sensitive label text")
    if any(part in key_folded for part in _FORBIDDEN_METADATA_KEY_PARTS):
        raise ValueError(f"{field_name} must not be sensitive label text")
    _reject_secret_shaped_value(field_name, value)


def _require_status(field_name: str, value: str, allowed: frozenset[str]) -> None:
    if value not in allowed:
        raise ValueError(f"{field_name} must be one of {sorted(allowed)}")


def _require_optional_display_text(field_name: str, value: str | None) -> None:
    if value is None:
        return
    _require_non_empty(field_name, value)
    if len(value) > _MAX_DISPLAY_TEXT_CHARS:
        raise ValueError(
            f"{field_name} must be at most {_MAX_DISPLAY_TEXT_CHARS} characters"
        )
    _reject_secret_shaped_value(field_name, value)


def _require_datetime(field_name: str, value: datetime) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive int")


def _require_json_object(field_name: str, value: JSONObject) -> None:
    if not isinstance(value, dict):
        raise TypeError(f"{field_name} must be a JSON object")
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(f"{field_name} keys must be strings")
        key_folded = key.casefold()
        if any(part in key_folded for part in _FORBIDDEN_METADATA_KEY_PARTS):
            raise ValueError(f"{field_name} contains forbidden metadata key: {key}")
        _require_json_value(f"{field_name}.{key}", item)


def _require_json_value(field_name: str, value: object) -> None:
    if value is None or isinstance(value, str | int | float | bool):
        if isinstance(value, str):
            if len(value) > _MAX_METADATA_STRING_CHARS:
                raise ValueError(
                    f"{field_name} must be at most {_MAX_METADATA_STRING_CHARS} characters"
                )
            _reject_secret_shaped_value(field_name, value)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _require_json_value(f"{field_name}[{index}]", item)
        return
    if isinstance(value, dict):
        _require_json_object(field_name, value)
        return
    raise TypeError(f"{field_name} must be JSON-safe")


def _reject_secret_shaped_value(field_name: str, value: str) -> None:
    folded = value.casefold()
    if any(
        marker in folded for marker in _SECRET_VALUE_MARKERS if marker != "sk-"
    ) or folded.startswith("sk-"):
        raise ValueError(f"{field_name} must not contain secret-shaped values")


def _default_trigger_id(schedule: ScheduleRecord, now: datetime) -> str:
    safe_time = now.isoformat().replace(":", "").replace("+", "_")
    return f"trigger-{schedule.schedule_id}-{safe_time}"


def _next_due_at(schedule: ScheduleRecord, now: datetime) -> datetime | None:
    if schedule.cadence == "once":
        return None
    if schedule.cadence == "hourly":
        return now + timedelta(hours=1)
    if schedule.cadence == "daily":
        return now + timedelta(days=1)
    return None


def _schedule_bee_launch_metadata(schedule: ScheduleRecord) -> JSONObject:
    value = schedule.metadata.get("bee_launch")
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError("schedule.metadata.bee_launch must be an object")
    bee_launch = dict(value)
    _require_json_object("schedule.metadata.bee_launch", bee_launch)
    return {"bee_launch": bee_launch}


def _require_topic_matches_intent(
    *,
    topic: TopicRecord,
    intent: ScheduledLaunchIntent,
    tape: Tape,
) -> None:
    if topic.session_id != intent.session_id:
        raise ValueError("scheduled topic session does not match launch intent")
    if topic.tape_id != tape.tape_id:
        raise ValueError("scheduled topic tape does not match launch tape")


def _safe_metadata_label(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    _require_safe_label("metadata_label", value)
    return value


def _required_signal_session(signal: ProactiveSignalRecord) -> str:
    if signal.session_id is None:
        raise ValueError("proactive signal requires session_id for launch planning")
    return signal.session_id


def _required_str(row: dict[str, object], key: str, *, context: str) -> str:
    value = row.get(key)
    if not isinstance(value, str):
        raise TypeError(f"postgres {context} must include string {key}")
    return value


def _optional_str(row: dict[str, object], key: str, *, context: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"postgres {context} must include string or None {key}")
    return value


def _required_datetime(row: dict[str, object], key: str, *, context: str) -> datetime:
    value = row.get(key)
    if not isinstance(value, datetime):
        raise TypeError(f"postgres {context} must include datetime {key}")
    return value


def _optional_datetime(
    row: dict[str, object], key: str, *, context: str
) -> datetime | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError(f"postgres {context} must include datetime or None {key}")
    return value


def _required_json_object(
    row: dict[str, object], key: str, *, context: str
) -> JSONObject:
    value = row.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"postgres {context} must include dict {key}")
    _require_json_object(key, value)
    return value
