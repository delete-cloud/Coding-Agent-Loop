from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from coding_agent.external_executor import (
    ExecutorCapability,
    ExecutorEvidence,
    ExecutorPlan,
    ExecutorRegistry,
    ExecutorResult,
    ExecutorRunRecord,
    PGExecutorRunStore,
)


class FakeExecutorRunPool:
    def __init__(self) -> None:
        self.runs: dict[str, dict[str, object]] = {}
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def get_pool(self) -> FakeExecutorRunPool:
        return self

    async def execute(self, query: str, *args: object) -> str:
        self.executed.append((query, args))
        if "CREATE TABLE IF NOT EXISTS executor_runs" in query:
            return "CREATE TABLE"
        raise AssertionError(f"unexpected execute query: {query}")

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        self.executed.append((query, args))
        if "INSERT INTO executor_runs" in query:
            row = _run_row(*args)
            if cast(str, row["executor_run_id"]) in self.runs:
                return None
            self.runs[cast(str, row["executor_run_id"])] = row
            return row
        if "SELECT * FROM executor_runs WHERE executor_run_id = $1" in query:
            return self.runs.get(cast(str, args[0]))
        if "UPDATE executor_runs" in query and "sanitized_summary = $6" in query:
            return self._attach_result(args)
        if "UPDATE executor_runs" in query:
            return self._update_status(args)
        raise AssertionError(f"unexpected fetchrow query: {query}")

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.executed.append((query, args))
        if "SELECT * FROM executor_runs" not in query:
            raise AssertionError(f"unexpected fetch query: {query}")
        task_id, node_id, executor_kind, status, limit = args
        rows = [
            row
            for row in self.runs.values()
            if (task_id is None or row["task_id"] == task_id)
            and (node_id is None or row["node_id"] == node_id)
            and (executor_kind is None or row["executor_kind"] == executor_kind)
            and (status is None or row["status"] == status)
        ]
        rows.sort(
            key=lambda row: (
                cast(datetime, row["requested_at"]),
                row["executor_run_id"],
            )
        )
        return rows[: cast(int, limit)]

    async def close(self) -> None:
        return None

    async def acquire(self) -> FakeExecutorRunPool:
        return self

    async def release(self, connection: object) -> None:
        if connection is not self:
            raise AssertionError("unexpected connection released")

    def _update_status(self, args: tuple[object, ...]) -> dict[str, object] | None:
        (
            executor_run_id,
            status,
            submitted_at,
            started_at,
            finished_at,
            error_type,
            error_message,
            metadata,
        ) = args
        row = self.runs.get(cast(str, executor_run_id))
        if row is None:
            return None
        row.update({
            "status": status,
            "submitted_at": submitted_at,
            "started_at": started_at,
            "finished_at": finished_at,
            "error_type": error_type,
            "error_message": error_message,
            "metadata": metadata,
        })
        return row

    def _attach_result(self, args: tuple[object, ...]) -> dict[str, object] | None:
        (
            executor_run_id,
            status,
            finished_at,
            error_type,
            error_message,
            sanitized_summary,
            evidence,
            metadata,
        ) = args
        row = self.runs.get(cast(str, executor_run_id))
        if row is None:
            return None
        row.update({
            "status": status,
            "finished_at": finished_at,
            "error_type": error_type,
            "error_message": error_message,
            "sanitized_summary": sanitized_summary,
            "evidence": evidence,
            "metadata": metadata,
        })
        return row


class FakeExecutor:
    kind = "fixture"

    def capability(self) -> ExecutorCapability:
        return ExecutorCapability(
            executor_kind="fixture",
            enabled=True,
            available=True,
            status="available",
            reason="fixture executor registered",
        )

    async def submit(self, plan: ExecutorPlan) -> ExecutorResult:
        return ExecutorResult(
            status="succeeded",
            sanitized_summary=f"{plan.executor_kind} plan accepted",
        )


@pytest.fixture
def fake_pool() -> FakeExecutorRunPool:
    return FakeExecutorRunPool()


@pytest.fixture
def store(fake_pool: FakeExecutorRunPool) -> PGExecutorRunStore:
    return PGExecutorRunStore(pool=fake_pool)  # type: ignore[arg-type]


def test_executor_registry_resolves_known_and_rejects_unknown_kind() -> None:
    registry = ExecutorRegistry()
    executor = FakeExecutor()

    registry.register(executor)

    assert registry.resolve("fixture") is executor
    assert registry.list_kinds() == ("fixture",)
    with pytest.raises(KeyError, match="executor not registered"):
        registry.resolve("local")
    with pytest.raises(ValueError, match="executor already registered"):
        registry.register(executor)
    with pytest.raises(ValueError, match="executor kind"):
        registry.resolve("unknown")


@pytest.mark.asyncio
async def test_executor_run_store_schema_is_idempotent(
    store: PGExecutorRunStore,
    fake_pool: FakeExecutorRunPool,
) -> None:
    await store.create_executor_run(_run())
    await store.load_executor_run("executor-run-1")
    await store.list_executor_runs(task_id="task-1")

    schema_calls = [
        query
        for query, _args in fake_pool.executed
        if "CREATE TABLE IF NOT EXISTS executor_runs" in query
    ]

    assert len(schema_calls) == 1
    assert "executor_runs_task_node_idx" in schema_calls[0]
    assert "executor_runs_kind_status_idx" in schema_calls[0]
    assert "bee_tasks" not in schema_calls[0]


@pytest.mark.asyncio
async def test_executor_run_store_create_update_attach_and_list(
    store: PGExecutorRunStore,
) -> None:
    created = await store.create_executor_run(_run())
    other = await store.create_executor_run(
        _run("executor-run-2", task_id="task-1", node_id="node-2")
    )
    updated = await store.update_executor_run_status(
        "executor-run-1",
        status="running",
        submitted_at=_dt(10),
        started_at=_dt(11),
        finished_at=None,
        error_type=None,
        error_message=None,
        metadata={"phase": "running"},
    )
    attached = await store.attach_executor_result(
        "executor-run-1",
        result=ExecutorResult(
            status="succeeded",
            sanitized_summary="Validation passed with sanitized evidence",
            finished_at=_dt(12),
            evidence=(
                ExecutorEvidence(
                    evidence_kind="validation_report",
                    evidence_ref="evidence/validation-report.md",
                    summary="Sanitized validation report",
                    metadata={"profile": "fixture"},
                ),
            ),
            metadata={"phase": "result_attached"},
        ),
    )
    loaded = await store.load_executor_run("executor-run-1")
    by_task_node = await store.list_executor_runs(task_id="task-1", node_id="node-1")
    by_kind_status = await store.list_executor_runs(
        executor_kind="fixture",
        status="planned",
    )

    assert created.executor_run_id == "executor-run-1"
    assert updated.status == "running"
    assert updated.started_at == _dt(11)
    assert attached.status == "succeeded"
    assert attached.finished_at == _dt(12)
    assert attached.sanitized_summary == "Validation passed with sanitized evidence"
    assert attached.evidence[0].evidence_ref == "evidence/validation-report.md"
    assert loaded == attached
    assert by_task_node == [attached]
    assert by_kind_status == [other]


@pytest.mark.asyncio
async def test_executor_run_store_duplicate_create_does_not_rewrite_attempt(
    store: PGExecutorRunStore,
) -> None:
    created = await store.create_executor_run(_run())

    with pytest.raises(ValueError, match="executor run already exists"):
        await store.create_executor_run(
            _run(
                task_id="different-task",
                node_id="different-node",
                status="running",
            )
        )

    assert await store.load_executor_run("executor-run-1") == created


def test_executor_records_reject_sensitive_metadata_and_raw_summaries() -> None:
    with pytest.raises(ValueError, match="executor kind"):
        _run(executor_kind="ssh")

    with pytest.raises(ValueError, match="forbidden metadata key"):
        _run(metadata={"stdout": "do not store"})

    with pytest.raises(ValueError, match="secret-shaped"):
        ExecutorResult(
            status="failed",
            sanitized_summary="token=secret leaked",
        )

    with pytest.raises(ValueError, match="secret-shaped"):
        ExecutorResult(
            status="failed",
            error_message="sk-test-secret leaked",
        )

    with pytest.raises(ValueError, match="secret-shaped"):
        ExecutorEvidence(
            evidence_kind="artifact",
            evidence_ref="evidence/report.md",
            summary="sk-test-secret leaked",
        )

    with pytest.raises(ValueError, match="secret-shaped"):
        _run(metadata={"phase": "sk-test-secret leaked"})

    with pytest.raises(ValueError, match="forbidden metadata key"):
        ExecutorEvidence(
            evidence_kind="artifact",
            evidence_ref="evidence/report.md",
            metadata={"raw_log": "unsafe"},
        )

    with pytest.raises(ValueError, match="sensitive reference text"):
        ExecutorEvidence(
            evidence_kind="artifact",
            evidence_ref="pods/raw-pod-name",
        )

    with pytest.raises(ValueError, match="secret-shaped"):
        ExecutorEvidence(
            evidence_kind="artifact",
            evidence_ref="evidence/token=secret.md",
        )


@pytest.mark.asyncio
async def test_executor_run_store_missing_rows_fail_fast(
    store: PGExecutorRunStore,
) -> None:
    with pytest.raises(KeyError, match="executor run not found"):
        await store.update_executor_run_status(
            "missing-run",
            status="failed",
            submitted_at=None,
            started_at=None,
            finished_at=_dt(12),
            error_type="not_found",
            error_message="Executor record missing",
            metadata={"phase": "test"},
        )

    with pytest.raises(KeyError, match="executor run not found"):
        await store.attach_executor_result(
            "missing-run",
            result=ExecutorResult(
                status="failed",
                sanitized_summary="Executor run missing",
                finished_at=_dt(12),
            ),
        )


def _run_row(*args: object) -> dict[str, object]:
    (
        executor_run_id,
        executor_kind,
        task_id,
        node_id,
        launch_id,
        topic_id,
        status,
        requested_at,
        submitted_at,
        started_at,
        finished_at,
        error_type,
        error_message,
        sanitized_summary,
        evidence,
        metadata,
    ) = args
    return {
        "executor_run_id": executor_run_id,
        "executor_kind": executor_kind,
        "task_id": task_id,
        "node_id": node_id,
        "launch_id": launch_id,
        "topic_id": topic_id,
        "status": status,
        "requested_at": requested_at,
        "submitted_at": submitted_at,
        "started_at": started_at,
        "finished_at": finished_at,
        "error_type": error_type,
        "error_message": error_message,
        "sanitized_summary": sanitized_summary,
        "evidence": evidence,
        "metadata": metadata,
    }


def _run(
    executor_run_id: str = "executor-run-1",
    *,
    executor_kind: str = "fixture",
    task_id: str = "task-1",
    node_id: str = "node-1",
    status: str = "planned",
    metadata: dict[str, object] | None = None,
) -> ExecutorRunRecord:
    return ExecutorRunRecord(
        executor_run_id=executor_run_id,
        executor_kind=executor_kind,
        task_id=task_id,
        node_id=node_id,
        launch_id="launch-1",
        topic_id="topic-1",
        status=status,
        requested_at=_dt(9),
        metadata=cast(dict[str, object], metadata or {"phase": "planned"}),
    )


def _dt(hour: int) -> datetime:
    return datetime(2026, 5, 23, hour, tzinfo=UTC)
