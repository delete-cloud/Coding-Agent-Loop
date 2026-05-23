"""Generic external executor records and registry.

External executors consume already-authorized Bee execution plans. This module
defines the product-layer model and durable run store; concrete executor
adapters are added in later goals.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
from inspect import isawaitable
from pathlib import Path
from typing import Final, Protocol

from agentkit.storage.pg import AsyncPGPool, PGPool
from coding_agent.action_safety.approval_routing import ActionApprovalRoute
from coding_agent.action_safety.command_policy import CommandPolicyDecision
from coding_agent.bee_command_bridge import (
    BeeCommandIntentPlan,
    BeeNodeCompletionEvidence,
    is_authorized_bee_command_plan,
)
from coding_agent.topic_store import JSONObject, JSONValue

ExecutorKind = str
ExecutorRunStatus = str
WorkspaceResolver = Callable[[str], Path | None]

_EXECUTOR_KINDS: Final[frozenset[str]] = frozenset({
    "local",
    "docker",
    "kubernetes_job",
    "argo_workflow",
    "fixture",
})
_EXECUTOR_RUN_STATUSES: Final[frozenset[str]] = frozenset({
    "planned",
    "submitted",
    "running",
    "succeeded",
    "failed",
    "cancelled",
})
_MAX_SAFE_LABEL_CHARS: Final[int] = 128
_MAX_DISPLAY_TEXT_CHARS: Final[int] = 256
_MAX_METADATA_STRING_CHARS: Final[int] = 256
_FORBIDDEN_METADATA_KEY_PARTS: Final[frozenset[str]] = frozenset({
    "api_key",
    "apikey",
    "args",
    "argv",
    "bearer",
    "cmd",
    "command",
    "commands",
    "command_output",
    "content",
    "credential",
    "credentials",
    "env",
    "environment",
    "exec",
    "key",
    "log",
    "message",
    "password",
    "pod_name",
    "prompt",
    "result",
    "script",
    "secret",
    "shell",
    "stderr",
    "stdout",
    "text",
    "token",
    "workflow_name",
    "job_name",
})
_FORBIDDEN_LABEL_VALUE_PARTS: Final[frozenset[str]] = frozenset({
    "api_key",
    "apikey",
    "bearer",
    "command_output",
    "content",
    "credential",
    "credentials",
    "env",
    "environment",
    "key",
    "message",
    "password",
    "prompt",
    "result",
    "secret",
    "stderr",
    "stdout",
    "text",
    "token",
})
_FORBIDDEN_REF_PARTS: Final[frozenset[str]] = frozenset({
    "job_name",
    "jobs/",
    "kubeconfig",
    "password",
    "pod_name",
    "pods/",
    "secret",
    "token",
    "workflow_name",
    "workflows/",
})
_RAW_EXECUTION_TEXT_MARKERS: Final[tuple[str, ...]] = (
    "command:",
    "command=",
    "raw log",
    "raw_log",
    "rm -rf",
    "stderr:",
    "stderr=",
    "stdout:",
    "stdout=",
    "traceback",
    "uv run",
    "python -c",
    "pytest ",
    "pytest -q",
)
_SECRET_VALUE_MARKERS: Final[tuple[str, ...]] = (
    "-----begin ",
    "akia",
    "bearer ",
    "gho_",
    "ghp_",
    "kubeconfig",
    "password=",
    "private key",
    "secret=",
    "sk-",
    "token=",
)
_EXECUTOR_PLAN_AUTHORIZATION_TOKEN: Final[object] = object()


class ExternalExecutor(Protocol):
    @property
    def kind(self) -> ExecutorKind: ...

    def capability(self) -> ExecutorCapability: ...

    async def submit(self, plan: ExecutorPlan) -> ExecutorResult: ...


class DockerCapabilityClient(Protocol):
    def available(self) -> bool: ...


class ExecutorRunStore(Protocol):
    async def create_executor_run(
        self,
        record: ExecutorRunRecord,
    ) -> ExecutorRunRecord: ...

    async def load_executor_run(
        self,
        executor_run_id: str,
    ) -> ExecutorRunRecord | None: ...

    async def list_executor_runs(
        self,
        *,
        task_id: str | None = None,
        node_id: str | None = None,
        executor_kind: ExecutorKind | None = None,
        status: ExecutorRunStatus | None = None,
        limit: int = 100,
    ) -> list[ExecutorRunRecord]: ...

    async def update_executor_run_status(
        self,
        executor_run_id: str,
        *,
        status: ExecutorRunStatus,
        submitted_at: datetime | None,
        started_at: datetime | None,
        finished_at: datetime | None,
        error_type: str | None,
        error_message: str | None,
        metadata: JSONObject,
    ) -> ExecutorRunRecord: ...

    async def attach_executor_result(
        self,
        executor_run_id: str,
        *,
        result: ExecutorResult,
    ) -> ExecutorRunRecord: ...


@dataclass(frozen=True)
class ExecutorCapability:
    executor_kind: ExecutorKind
    enabled: bool
    available: bool
    status: str
    reason: str | None = None
    metadata: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_executor_kind(self.executor_kind)
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a bool")
        if not isinstance(self.available, bool):
            raise TypeError("available must be a bool")
        _require_optional_label("status", self.status)
        _require_optional_display_text("reason", self.reason)
        _require_json_object("metadata", self.metadata)


@dataclass(frozen=True)
class ExecutorPlan:
    executor_kind: ExecutorKind
    task_id: str
    node_id: str
    workspace_ref: str
    requested_at: datetime
    executor_run_id: str | None = None
    approved_workspace_hash: str | None = None
    command_category: str = "unknown"
    command_profile: str = "unknown"
    launch_id: str | None = None
    topic_id: str | None = None
    timeout_seconds: int | None = None
    validation_label: str | None = None
    metadata: JSONObject = field(default_factory=dict)
    authorization_token: object | None = field(default=None, repr=False, compare=False)
    authorization_signature: str | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        _require_executor_kind(self.executor_kind)
        _require_non_empty("task_id", self.task_id)
        _require_non_empty("node_id", self.node_id)
        _require_optional_label("workspace_ref", self.workspace_ref)
        _require_datetime("requested_at", self.requested_at)
        _require_optional_id("executor_run_id", self.executor_run_id)
        _require_optional_id("approved_workspace_hash", self.approved_workspace_hash)
        _require_optional_label("command_category", self.command_category)
        _require_optional_label("command_profile", self.command_profile)
        _require_optional_id("launch_id", self.launch_id)
        _require_optional_id("topic_id", self.topic_id)
        if self.timeout_seconds is not None:
            _require_positive_int("timeout_seconds", self.timeout_seconds)
        _require_optional_label("validation_label", self.validation_label)
        _require_json_object("metadata", self.metadata)


@dataclass(frozen=True)
class ExecutorEvidence:
    evidence_kind: str
    evidence_ref: str
    summary: str | None = None
    metadata: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_optional_label("evidence_kind", self.evidence_kind)
        _require_safe_ref("evidence_ref", self.evidence_ref)
        _require_optional_display_text("summary", self.summary)
        _require_json_object("metadata", self.metadata)

    def to_safe_dict(self) -> JSONObject:
        data: JSONObject = {
            "evidence_kind": self.evidence_kind,
            "evidence_ref": self.evidence_ref,
            "metadata": dict(self.metadata),
        }
        if self.summary is not None:
            data["summary"] = self.summary
        return data


@dataclass(frozen=True)
class ExecutorResult:
    status: ExecutorRunStatus
    sanitized_summary: str | None = None
    evidence: tuple[ExecutorEvidence, ...] = ()
    finished_at: datetime | None = None
    error_type: str | None = None
    error_message: str | None = None
    metadata: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_status("executor result status", self.status)
        _require_optional_display_text("sanitized_summary", self.sanitized_summary)
        _require_optional_datetime("finished_at", self.finished_at)
        _require_optional_label("error_type", self.error_type)
        _require_optional_display_text("error_message", self.error_message)
        if not isinstance(self.evidence, tuple):
            raise TypeError("evidence must be a tuple")
        for item in self.evidence:
            if not isinstance(item, ExecutorEvidence):
                raise TypeError("evidence items must be ExecutorEvidence")
        _require_json_object("metadata", self.metadata)

    def evidence_as_json(self) -> list[JSONObject]:
        return [item.to_safe_dict() for item in self.evidence]


LocalExecutorRunner = Callable[
    [ExecutorPlan, Path],
    ExecutorResult | Awaitable[ExecutorResult],
]


@dataclass(frozen=True)
class ExecutorRunRecord:
    executor_run_id: str
    executor_kind: ExecutorKind
    task_id: str
    node_id: str
    status: ExecutorRunStatus
    requested_at: datetime
    launch_id: str | None = None
    topic_id: str | None = None
    submitted_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_type: str | None = None
    error_message: str | None = None
    sanitized_summary: str | None = None
    evidence: tuple[ExecutorEvidence, ...] = ()
    metadata: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("executor_run_id", self.executor_run_id)
        _require_executor_kind(self.executor_kind)
        _require_non_empty("task_id", self.task_id)
        _require_non_empty("node_id", self.node_id)
        _require_status("executor run status", self.status)
        _require_datetime("requested_at", self.requested_at)
        _require_optional_id("launch_id", self.launch_id)
        _require_optional_id("topic_id", self.topic_id)
        _require_optional_datetime("submitted_at", self.submitted_at)
        _require_optional_datetime("started_at", self.started_at)
        _require_optional_datetime("finished_at", self.finished_at)
        _require_optional_label("error_type", self.error_type)
        _require_optional_display_text("error_message", self.error_message)
        _require_optional_display_text("sanitized_summary", self.sanitized_summary)
        if not isinstance(self.evidence, tuple):
            raise TypeError("evidence must be a tuple")
        for item in self.evidence:
            if not isinstance(item, ExecutorEvidence):
                raise TypeError("evidence items must be ExecutorEvidence")
        _require_json_object("metadata", self.metadata)


class ExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[ExecutorKind, ExternalExecutor] = {}

    def register(self, executor: ExternalExecutor) -> None:
        _require_executor_kind(executor.kind)
        if executor.kind in self._executors:
            raise ValueError(f"executor already registered: {executor.kind}")
        self._executors[executor.kind] = executor

    def resolve(self, executor_kind: ExecutorKind) -> ExternalExecutor:
        _require_executor_kind(executor_kind)
        executor = self._executors.get(executor_kind)
        if executor is None:
            raise KeyError(f"executor not registered: {executor_kind}")
        return executor

    def list_kinds(self) -> tuple[ExecutorKind, ...]:
        return tuple(sorted(self._executors))


class LocalExecutorAdapter:
    """Local executor adapter over already-approved Bee command bridge plans."""

    kind: Final[ExecutorKind] = "local"

    def __init__(
        self,
        *,
        store: ExecutorRunStore,
        runner: LocalExecutorRunner | None = None,
        workspace_resolver: WorkspaceResolver | None = None,
        now: Callable[[], datetime],
        executor_run_id_factory: Callable[[ExecutorPlan], str] | None = None,
    ) -> None:
        self._store = store
        self._runner = runner
        self._workspace_resolver = workspace_resolver
        self._now = now
        self._executor_run_id_factory = (
            executor_run_id_factory or _default_executor_run_id
        )

    def capability(self) -> ExecutorCapability:
        available = self._runner is not None and self._workspace_resolver is not None
        return ExecutorCapability(
            executor_kind=self.kind,
            enabled=True,
            available=available,
            status="available" if available else "unavailable",
            reason=None if available else "local executor dependencies not configured",
        )

    async def submit(self, plan: ExecutorPlan) -> ExecutorResult:
        _require_local_executor_plan(plan)
        workspace_root = self._resolve_workspace(plan)
        if workspace_root is None:
            raise ValueError(
                f"local executor workspace not found: {plan.workspace_ref}"
            )
        _require_approved_workspace_binding(plan, workspace_root)
        if self._runner is None:
            raise ValueError("local executor runner is not configured")
        executor_run_id = plan.executor_run_id or self._executor_run_id_factory(plan)
        requested_at = plan.requested_at
        await self._store.create_executor_run(
            ExecutorRunRecord(
                executor_run_id=executor_run_id,
                executor_kind=self.kind,
                task_id=plan.task_id,
                node_id=plan.node_id,
                launch_id=plan.launch_id,
                topic_id=plan.topic_id,
                status="planned",
                requested_at=requested_at,
                metadata=_executor_run_metadata(plan),
            )
        )
        started_at = self._now()
        await self._store.update_executor_run_status(
            executor_run_id,
            status="running",
            submitted_at=started_at,
            started_at=started_at,
            finished_at=None,
            error_type=None,
            error_message=None,
            metadata={"phase": "local_runner_started"},
        )
        try:
            result_or_awaitable = self._runner(plan, workspace_root)
            result = (
                await result_or_awaitable
                if isawaitable(result_or_awaitable)
                else result_or_awaitable
            )
            result = _sanitize_local_executor_result(result)
        except ValueError as exc:
            if _is_safety_validation_error(exc):
                raise
            result = ExecutorResult(
                status="failed",
                sanitized_summary="Local executor failed with sanitized error",
                finished_at=self._now(),
                error_type=exc.__class__.__name__,
                error_message="local executor runner failed",
                metadata={"phase": "local_runner_failed"},
            )
        except Exception as exc:
            result = ExecutorResult(
                status="failed",
                sanitized_summary="Local executor failed with sanitized error",
                finished_at=self._now(),
                error_type=exc.__class__.__name__,
                error_message="local executor runner failed",
                metadata={"phase": "local_runner_failed"},
            )
        await self._store.attach_executor_result(executor_run_id, result=result)
        return result

    def _resolve_workspace(self, plan: ExecutorPlan) -> Path | None:
        if self._workspace_resolver is None:
            return None
        workspace_root = self._workspace_resolver(plan.workspace_ref)
        if workspace_root is None:
            return None
        resolved = Path(workspace_root).expanduser().resolve()
        if not resolved.is_dir():
            return None
        return resolved


class DockerExecutorAdapter:
    """Optional Docker executor adapter in disabled/dry-run mode."""

    kind: Final[ExecutorKind] = "docker"

    def __init__(
        self,
        *,
        enabled: bool = False,
        capability_client: DockerCapabilityClient | None = None,
    ) -> None:
        self._enabled = enabled
        self._capability_client = capability_client

    def capability(self) -> ExecutorCapability:
        if not self._enabled:
            return ExecutorCapability(
                executor_kind=self.kind,
                enabled=False,
                available=False,
                status="disabled",
                reason="docker executor disabled",
            )
        available = (
            self._capability_client.available()
            if self._capability_client is not None
            else False
        )
        return ExecutorCapability(
            executor_kind=self.kind,
            enabled=True,
            available=available,
            status="available" if available else "unavailable",
            reason=None if available else "docker capability unavailable",
        )

    async def submit(self, plan: ExecutorPlan) -> ExecutorResult:
        raise RuntimeError("docker executor execution is deferred; use dry_run_render")

    def dry_run_render(self, plan: ExecutorPlan) -> JSONObject:
        _require_authorized_executor_plan(plan, expected_kind=self.kind)
        return {
            "executor_kind": self.kind,
            "mode": "dry_run",
            "task_ref": _safe_ref_token(plan.task_id),
            "node_ref": _safe_ref_token(plan.node_id),
            "workspace_ref": _safe_ref_token(plan.workspace_ref),
            "intent_category": plan.command_category,
            "intent_profile": plan.command_profile,
            "timeout_seconds": plan.timeout_seconds or 0,
        }


def build_local_executor_plan_from_bee_command_plan(
    *,
    command_plan: BeeCommandIntentPlan,
    task_id: str,
    workspace_ref: str,
    approved_workspace_root: Path | str,
    requested_at: datetime,
    executor_run_id: str | None = None,
    launch_id: str | None = None,
    topic_id: str | None = None,
    timeout_seconds: int | None = None,
) -> ExecutorPlan:
    if command_plan.status != "ready":
        raise ValueError("local executor requires a ready Bee command plan")
    if not is_authorized_bee_command_plan(command_plan):
        raise ValueError("local executor requires authorized Bee command plan")
    if command_plan.policy is None:
        raise ValueError("local executor requires command policy verdict")
    if command_plan.policy.decision != CommandPolicyDecision.ALLOW:
        raise ValueError("local executor requires allowed command policy")
    if command_plan.approval_route is None:
        raise ValueError("local executor requires approval route")
    if command_plan.approval_route.route != ActionApprovalRoute.ALLOW:
        raise ValueError("local executor requires allow approval route")
    intent = command_plan.resolution.intent
    if intent is None:
        raise ValueError("local executor requires resolved command intent")
    return _authorize_executor_plan(
        ExecutorPlan(
            executor_kind="local",
            executor_run_id=executor_run_id,
            approved_workspace_hash=_workspace_hash(
                Path(approved_workspace_root).expanduser().resolve()
            ),
            task_id=task_id,
            node_id=command_plan.resolution.node_id,
            workspace_ref=workspace_ref,
            requested_at=requested_at,
            command_category=intent.category,
            command_profile=intent.profile,
            launch_id=launch_id,
            topic_id=topic_id,
            timeout_seconds=timeout_seconds or command_plan.policy.timeout_seconds,
            validation_label=intent.validation_label or intent.name,
            metadata={
                "policy_decision": command_plan.policy.decision.value,
                "approval_route": command_plan.approval_route.route.value,
                "template_id": command_plan.resolution.template_id,
                "runtime_kind": command_plan.policy.environment_kind,
            },
            authorization_token=_EXECUTOR_PLAN_AUTHORIZATION_TOKEN,
        )
    )


def build_docker_executor_plan_from_local_plan(
    local_plan: ExecutorPlan,
    *,
    executor_run_id: str | None = None,
) -> ExecutorPlan:
    _require_authorized_executor_plan(local_plan, expected_kind="local")
    return _authorize_executor_plan(
        ExecutorPlan(
            executor_kind="docker",
            executor_run_id=executor_run_id,
            approved_workspace_hash=local_plan.approved_workspace_hash,
            task_id=local_plan.task_id,
            node_id=local_plan.node_id,
            workspace_ref=local_plan.workspace_ref,
            requested_at=local_plan.requested_at,
            command_category=local_plan.command_category,
            command_profile=local_plan.command_profile,
            launch_id=local_plan.launch_id,
            topic_id=local_plan.topic_id,
            timeout_seconds=local_plan.timeout_seconds,
            validation_label=local_plan.validation_label,
            metadata={
                "policy_decision": "allow",
                "approval_route": "allow",
                "source_kind": "local",
            },
            authorization_token=_EXECUTOR_PLAN_AUTHORIZATION_TOKEN,
        )
    )


def executor_result_completion_evidence(
    result: ExecutorResult,
) -> tuple[BeeNodeCompletionEvidence, ...]:
    status = "passed" if result.status == "succeeded" else "failed"
    return tuple(
        BeeNodeCompletionEvidence(
            evidence_kind=item.evidence_kind,
            evidence_ref=item.evidence_ref,
            status=status,
        )
        for item in result.evidence
    )


def _sanitize_local_executor_result(result: ExecutorResult) -> ExecutorResult:
    return ExecutorResult(
        status=result.status,
        sanitized_summary=_local_executor_status_summary(result.status),
        evidence=tuple(
            evidence for item in result.evidence if (evidence := _safe_evidence(item))
        ),
        finished_at=result.finished_at,
        error_type=result.error_type,
        error_message=_local_executor_error_summary(result),
        metadata={"phase": "local_runner_result"},
    )


def _local_executor_status_summary(status: ExecutorRunStatus) -> str:
    if status == "succeeded":
        return "Local executor succeeded"
    if status == "failed":
        return "Local executor failed"
    if status == "cancelled":
        return "Local executor cancelled"
    return "Local executor status recorded"


def _local_executor_error_summary(result: ExecutorResult) -> str | None:
    if result.status == "failed":
        return "local executor returned failed status"
    return None


def _safe_evidence(item: ExecutorEvidence) -> ExecutorEvidence | None:
    try:
        return ExecutorEvidence(
            evidence_kind=item.evidence_kind,
            evidence_ref=item.evidence_ref,
        )
    except ValueError:
        return None


class PGExecutorRunStore:
    _CREATE_SCHEMA_SQL: Final[str] = """
    CREATE TABLE IF NOT EXISTS executor_runs (
        executor_run_id TEXT PRIMARY KEY,
        executor_kind TEXT NOT NULL,
        task_id TEXT NOT NULL,
        node_id TEXT NOT NULL,
        launch_id TEXT,
        topic_id TEXT,
        status TEXT NOT NULL,
        requested_at TIMESTAMPTZ NOT NULL,
        submitted_at TIMESTAMPTZ,
        started_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ,
        error_type TEXT,
        error_message TEXT,
        sanitized_summary TEXT,
        evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb
    );

    CREATE INDEX IF NOT EXISTS executor_runs_task_node_idx
        ON executor_runs (task_id, node_id, requested_at, executor_run_id);

    CREATE INDEX IF NOT EXISTS executor_runs_kind_status_idx
        ON executor_runs (executor_kind, status, requested_at, executor_run_id);
    """
    _INSERT_RUN_SQL: Final[str] = """
    INSERT INTO executor_runs (
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
        metadata
    )
    VALUES (
        $1, $2, $3, $4, $5, $6, $7, $8,
        $9, $10, $11, $12, $13, $14, $15::jsonb, $16::jsonb
    )
    ON CONFLICT (executor_run_id) DO NOTHING
    RETURNING *
    """
    _SELECT_RUN_SQL: Final[str] = """
    SELECT * FROM executor_runs WHERE executor_run_id = $1
    """
    _LIST_RUNS_SQL: Final[str] = """
    SELECT * FROM executor_runs
    WHERE ($1::text IS NULL OR task_id = $1)
      AND ($2::text IS NULL OR node_id = $2)
      AND ($3::text IS NULL OR executor_kind = $3)
      AND ($4::text IS NULL OR status = $4)
    ORDER BY requested_at, executor_run_id
    LIMIT $5
    """
    _UPDATE_RUN_STATUS_SQL: Final[str] = """
    UPDATE executor_runs
    SET status = $2,
        submitted_at = $3,
        started_at = $4,
        finished_at = $5,
        error_type = $6,
        error_message = $7,
        metadata = $8::jsonb
    WHERE executor_run_id = $1
    RETURNING *
    """
    _ATTACH_RESULT_SQL: Final[str] = """
    UPDATE executor_runs
    SET status = $2,
        finished_at = $3,
        error_type = $4,
        error_message = $5,
        sanitized_summary = $6,
        evidence = $7::jsonb,
        metadata = $8::jsonb
    WHERE executor_run_id = $1
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

    async def create_executor_run(
        self,
        record: ExecutorRunRecord,
    ) -> ExecutorRunRecord:
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._INSERT_RUN_SQL,
            record.executor_run_id,
            record.executor_kind,
            record.task_id,
            record.node_id,
            record.launch_id,
            record.topic_id,
            record.status,
            record.requested_at,
            record.submitted_at,
            record.started_at,
            record.finished_at,
            record.error_type,
            record.error_message,
            record.sanitized_summary,
            [item.to_safe_dict() for item in record.evidence],
            record.metadata,
        )
        if row is None:
            raise ValueError(f"executor run already exists: {record.executor_run_id}")
        return _run_from_row(row)

    async def load_executor_run(
        self,
        executor_run_id: str,
    ) -> ExecutorRunRecord | None:
        _require_non_empty("executor_run_id", executor_run_id)
        pool = await self._ensure_schema()
        row = await pool.fetchrow(self._SELECT_RUN_SQL, executor_run_id)
        return None if row is None else _run_from_row(row)

    async def list_executor_runs(
        self,
        *,
        task_id: str | None = None,
        node_id: str | None = None,
        executor_kind: ExecutorKind | None = None,
        status: ExecutorRunStatus | None = None,
        limit: int = 100,
    ) -> list[ExecutorRunRecord]:
        _require_optional_id("task_id", task_id)
        _require_optional_id("node_id", node_id)
        if executor_kind is not None:
            _require_executor_kind(executor_kind)
        if status is not None:
            _require_status("executor run status", status)
        _require_positive_int("limit", limit)
        pool = await self._ensure_schema()
        rows = await pool.fetch(
            self._LIST_RUNS_SQL,
            task_id,
            node_id,
            executor_kind,
            status,
            limit,
        )
        return [_run_from_row(row) for row in rows]

    async def update_executor_run_status(
        self,
        executor_run_id: str,
        *,
        status: ExecutorRunStatus,
        submitted_at: datetime | None,
        started_at: datetime | None,
        finished_at: datetime | None,
        error_type: str | None,
        error_message: str | None,
        metadata: JSONObject,
    ) -> ExecutorRunRecord:
        _require_non_empty("executor_run_id", executor_run_id)
        _require_status("executor run status", status)
        _require_optional_datetime("submitted_at", submitted_at)
        _require_optional_datetime("started_at", started_at)
        _require_optional_datetime("finished_at", finished_at)
        _require_optional_label("error_type", error_type)
        _require_optional_display_text("error_message", error_message)
        _require_json_object("metadata", metadata)
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._UPDATE_RUN_STATUS_SQL,
            executor_run_id,
            status,
            submitted_at,
            started_at,
            finished_at,
            error_type,
            error_message,
            metadata,
        )
        if row is None:
            raise KeyError(f"executor run not found: {executor_run_id}")
        return _run_from_row(row)

    async def attach_executor_result(
        self,
        executor_run_id: str,
        *,
        result: ExecutorResult,
    ) -> ExecutorRunRecord:
        _require_non_empty("executor_run_id", executor_run_id)
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._ATTACH_RESULT_SQL,
            executor_run_id,
            result.status,
            result.finished_at,
            result.error_type,
            result.error_message,
            result.sanitized_summary,
            result.evidence_as_json(),
            result.metadata,
        )
        if row is None:
            raise KeyError(f"executor run not found: {executor_run_id}")
        return _run_from_row(row)


def _run_from_row(row: dict[str, object]) -> ExecutorRunRecord:
    return ExecutorRunRecord(
        executor_run_id=_required_str(row, "executor_run_id", context="executor run"),
        executor_kind=_required_str(row, "executor_kind", context="executor run"),
        task_id=_required_str(row, "task_id", context="executor run"),
        node_id=_required_str(row, "node_id", context="executor run"),
        launch_id=_optional_str(row, "launch_id", context="executor run"),
        topic_id=_optional_str(row, "topic_id", context="executor run"),
        status=_required_str(row, "status", context="executor run"),
        requested_at=_required_datetime(row, "requested_at", context="executor run"),
        submitted_at=_optional_datetime(row, "submitted_at", context="executor run"),
        started_at=_optional_datetime(row, "started_at", context="executor run"),
        finished_at=_optional_datetime(row, "finished_at", context="executor run"),
        error_type=_optional_str(row, "error_type", context="executor run"),
        error_message=_optional_str(row, "error_message", context="executor run"),
        sanitized_summary=_optional_str(
            row, "sanitized_summary", context="executor run"
        ),
        evidence=_required_evidence_tuple(row, "evidence", context="executor run"),
        metadata=_required_json_object(row, "metadata", context="executor run"),
    )


def _required_evidence_tuple(
    row: dict[str, object],
    key: str,
    *,
    context: str,
) -> tuple[ExecutorEvidence, ...]:
    value = row.get(key)
    if not isinstance(value, list):
        raise TypeError(f"postgres {context} must include list {key}")
    return tuple(_evidence_from_json(item, context=context) for item in value)


def _evidence_from_json(value: object, *, context: str) -> ExecutorEvidence:
    if not isinstance(value, dict):
        raise TypeError(f"postgres {context} evidence must include objects")
    evidence_kind = value.get("evidence_kind")
    evidence_ref = value.get("evidence_ref")
    summary = value.get("summary")
    metadata = value.get("metadata", {})
    if not isinstance(evidence_kind, str):
        raise TypeError(f"postgres {context} evidence missing evidence_kind")
    if not isinstance(evidence_ref, str):
        raise TypeError(f"postgres {context} evidence missing evidence_ref")
    if summary is not None and not isinstance(summary, str):
        raise TypeError(f"postgres {context} evidence summary must be a string")
    if not isinstance(metadata, dict):
        raise TypeError(f"postgres {context} evidence metadata must be an object")
    return ExecutorEvidence(
        evidence_kind=evidence_kind,
        evidence_ref=evidence_ref,
        summary=summary,
        metadata=metadata,
    )


def _require_executor_kind(value: str) -> None:
    if value not in _EXECUTOR_KINDS:
        raise ValueError(f"executor kind must be one of {sorted(_EXECUTOR_KINDS)}")


def _require_local_executor_plan(plan: ExecutorPlan) -> None:
    _require_authorized_executor_plan(plan, expected_kind="local")
    if plan.approved_workspace_hash is None:
        raise ValueError("local executor requires approved workspace binding")


def _require_authorized_executor_plan(
    plan: ExecutorPlan,
    *,
    expected_kind: ExecutorKind,
) -> None:
    if plan.executor_kind != expected_kind:
        raise ValueError(
            f"{expected_kind} executor requires executor_kind={expected_kind!r}"
        )
    if plan.authorization_token is not _EXECUTOR_PLAN_AUTHORIZATION_TOKEN:
        raise ValueError(f"{expected_kind} executor requires authorized executor plan")
    if plan.authorization_signature != _executor_plan_signature(plan):
        raise ValueError(f"{expected_kind} executor requires signed executor plan")
    if plan.metadata.get("policy_decision") != "allow":
        raise ValueError(f"{expected_kind} executor requires allowed command policy")
    if plan.metadata.get("approval_route") != "allow":
        raise ValueError(f"{expected_kind} executor requires allow approval route")


def _require_approved_workspace_binding(
    plan: ExecutorPlan, workspace_root: Path
) -> None:
    if plan.approved_workspace_hash != _workspace_hash(workspace_root):
        raise ValueError("local executor workspace binding mismatch")


def _executor_run_metadata(plan: ExecutorPlan) -> JSONObject:
    return {
        "phase": "local_executor_planned",
        "intent_category": plan.command_category,
        "intent_profile": plan.command_profile,
    }


def _default_executor_run_id(plan: ExecutorPlan) -> str:
    return f"executor-{plan.task_id}-{plan.node_id}"


def _authorize_executor_plan(plan: ExecutorPlan) -> ExecutorPlan:
    object.__setattr__(
        plan,
        "authorization_signature",
        _executor_plan_signature(plan),
    )
    return plan


def _executor_plan_signature(plan: ExecutorPlan) -> str:
    metadata_items = tuple(
        sorted((key, str(value)) for key, value in plan.metadata.items())
    )
    parts = (
        plan.executor_kind,
        plan.executor_run_id or "",
        plan.approved_workspace_hash or "",
        plan.task_id,
        plan.node_id,
        plan.workspace_ref,
        plan.command_category,
        plan.command_profile,
        plan.launch_id or "",
        plan.topic_id or "",
        str(plan.timeout_seconds or ""),
        plan.validation_label or "",
        repr(metadata_items),
    )
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _workspace_hash(path: Path) -> str:
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]


def _safe_ref_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _is_safety_validation_error(exc: ValueError) -> bool:
    message = str(exc)
    return (
        "raw execution text" in message
        or "secret-shaped" in message
        or "forbidden metadata key" in message
        or "sensitive reference text" in message
        or "must not contain whitespace" in message
    )


def _require_non_empty(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_optional_id(field_name: str, value: str | None) -> None:
    if value is None:
        return
    _require_non_empty(field_name, value)


def _require_optional_label(field_name: str, value: str | None) -> None:
    if value is None:
        return
    _require_non_empty(field_name, value)
    if len(value) > _MAX_SAFE_LABEL_CHARS:
        raise ValueError(
            f"{field_name} must be at most {_MAX_SAFE_LABEL_CHARS} characters"
        )
    folded = value.casefold()
    if any(part in folded for part in _FORBIDDEN_LABEL_VALUE_PARTS):
        raise ValueError(f"{field_name} must not contain sensitive label text")
    _reject_secret_shaped_value(field_name, value)


def _require_safe_ref(field_name: str, value: str) -> None:
    _require_non_empty(field_name, value)
    if len(value) > _MAX_DISPLAY_TEXT_CHARS:
        raise ValueError(
            f"{field_name} must be at most {_MAX_DISPLAY_TEXT_CHARS} characters"
        )
    if any(character.isspace() for character in value):
        raise ValueError(f"{field_name} must not contain whitespace")
    _reject_secret_shaped_value(field_name, value)
    folded = value.casefold()
    if any(part in folded for part in _FORBIDDEN_REF_PARTS):
        raise ValueError(f"{field_name} must not contain sensitive reference text")
    _reject_raw_execution_text(field_name, value)


def _require_status(field_name: str, value: str) -> None:
    if value not in _EXECUTOR_RUN_STATUSES:
        raise ValueError(
            f"{field_name} must be one of {sorted(_EXECUTOR_RUN_STATUSES)}"
        )


def _require_datetime(field_name: str, value: datetime) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")


def _require_optional_datetime(field_name: str, value: datetime | None) -> None:
    if value is not None:
        _require_datetime(field_name, value)


def _require_optional_display_text(field_name: str, value: str | None) -> None:
    if value is None:
        return
    _require_non_empty(field_name, value)
    if len(value) > _MAX_DISPLAY_TEXT_CHARS:
        raise ValueError(
            f"{field_name} must be at most {_MAX_DISPLAY_TEXT_CHARS} characters"
        )
    _reject_secret_shaped_value(field_name, value)
    _reject_raw_execution_text(field_name, value)


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


def _require_json_value(field_name: str, value: JSONValue) -> None:
    if value is None or isinstance(value, str | int | float | bool):
        if isinstance(value, str):
            if len(value) > _MAX_METADATA_STRING_CHARS:
                raise ValueError(
                    f"{field_name} must be at most {_MAX_METADATA_STRING_CHARS} characters"
                )
            _reject_secret_shaped_value(field_name, value)
            _reject_raw_execution_text(field_name, value)
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
    if any(marker in folded for marker in _SECRET_VALUE_MARKERS):
        raise ValueError(f"{field_name} must not contain secret-shaped values")


def _reject_raw_execution_text(field_name: str, value: str) -> None:
    folded = value.casefold()
    if any(marker in folded for marker in _RAW_EXECUTION_TEXT_MARKERS):
        raise ValueError(f"{field_name} must not contain raw execution text")


def _required_row(row: dict[str, object] | None, context: str) -> dict[str, object]:
    if row is None:
        raise RuntimeError(f"postgres did not return a row for {context}")
    return row


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
    row: dict[str, object],
    key: str,
    *,
    context: str,
) -> datetime | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError(f"postgres {context} must include datetime or None {key}")
    return value


def _required_json_object(
    row: dict[str, object],
    key: str,
    *,
    context: str,
) -> JSONObject:
    value = row.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"postgres {context} must include dict {key}")
    _require_json_object(key, value)
    return value
