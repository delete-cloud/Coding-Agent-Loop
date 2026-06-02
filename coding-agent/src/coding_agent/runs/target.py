from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar, Literal, TypeAlias

from coding_agent.environment.execution_binding import (
    CloudWorkspaceBinding,
    ExecutionBinding,
    ExternalWorkerBinding,
    LocalAttachedExecutionBinding,
    LocalExecutionBinding,
)


def _require_non_empty(value: str, *, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")


def _empty_metadata() -> dict[str, object]:
    return {}


def _empty_annotations() -> dict[str, str]:
    return {}


def _copy_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    copied: dict[str, object] = {}
    for key, value in metadata.items():
        if not key.strip():
            raise ValueError("metadata keys must be non-empty")
        copied[key] = value
    return copied


def _copy_annotations(annotations: Mapping[str, str]) -> dict[str, str]:
    copied: dict[str, str] = {}
    for key, value in annotations.items():
        if not key.strip():
            raise ValueError("annotation keys must be non-empty")
        copied[key] = value
    return copied


@dataclass(frozen=True)
class LocalPathWorkspaceRef:
    path: str
    workspace_provider: str | None = None
    provider_instance_id: str | None = None
    kind: ClassVar[Literal["local_path"]] = "local_path"

    def __post_init__(self) -> None:
        _require_non_empty(self.path, field_name="workspace path")


@dataclass(frozen=True)
class CloudWorkspaceRef:
    workspace_url: str
    workspace_id: str
    runtime_profile: str | None = None
    workspace_provider: str | None = None
    provider_instance_id: str | None = None
    kind: ClassVar[Literal["cloud_workspace"]] = "cloud_workspace"

    def __post_init__(self) -> None:
        _require_non_empty(self.workspace_url, field_name="workspace_url")
        _require_non_empty(self.workspace_id, field_name="workspace_id")
        if self.runtime_profile is not None:
            _require_non_empty(self.runtime_profile, field_name="runtime_profile")


@dataclass(frozen=True)
class ExternalWorkerWorkspaceRef:
    ref: Mapping[str, object] = field(default_factory=_empty_metadata)
    provider_instance_id: str | None = None
    kind: ClassVar[Literal["external_worker_ref"]] = "external_worker_ref"

    def __post_init__(self) -> None:
        object.__setattr__(self, "ref", _copy_metadata(self.ref))


@dataclass(frozen=True)
class SnapshotWorkspaceRef:
    snapshot_id: str
    kind: ClassVar[Literal["snapshot"]] = "snapshot"

    def __post_init__(self) -> None:
        _require_non_empty(self.snapshot_id, field_name="snapshot_id")


WorkspaceRef: TypeAlias = (
    LocalPathWorkspaceRef
    | CloudWorkspaceRef
    | ExternalWorkerWorkspaceRef
    | SnapshotWorkspaceRef
)


@dataclass(frozen=True)
class LocalDaemonExecutorRef:
    kind: ClassVar[Literal["local_daemon"]] = "local_daemon"


@dataclass(frozen=True)
class ManagedPoolExecutorRef:
    pool: str = "default"
    kind: ClassVar[Literal["managed_pool"]] = "managed_pool"

    def __post_init__(self) -> None:
        _require_non_empty(self.pool, field_name="managed executor pool")


@dataclass(frozen=True)
class ExternalWorkerExecutorRef:
    executor_kind: str
    worker_pool: str = "default"
    kind: ClassVar[Literal["external_worker"]] = "external_worker"

    def __post_init__(self) -> None:
        _require_non_empty(self.executor_kind, field_name="executor_kind")
        _require_non_empty(self.worker_pool, field_name="worker_pool")


@dataclass(frozen=True)
class LocalAttachedExecutorRef:
    executor_kind: str
    worker_pool: str = "default"
    kind: ClassVar[Literal["local_attached"]] = "local_attached"

    def __post_init__(self) -> None:
        _require_non_empty(self.executor_kind, field_name="executor_kind")
        _require_non_empty(self.worker_pool, field_name="worker_pool")


@dataclass(frozen=True)
class InlineExecutorRef:
    kind: ClassVar[Literal["inline_testkit"]] = "inline_testkit"


ExecutorRef: TypeAlias = (
    LocalDaemonExecutorRef
    | ManagedPoolExecutorRef
    | ExternalWorkerExecutorRef
    | LocalAttachedExecutorRef
    | InlineExecutorRef
)


@dataclass(frozen=True)
class IsolationPolicy:
    kind: Literal[
        "default_local_sandbox",
        "provider_sandbox",
        "external_worker_policy",
        "dev_unsafe_disabled",
    ]
    network: Literal["restricted", "provider_managed", "unrestricted"] = "restricted"
    filesystem: Literal["workspace_scoped", "provider_managed", "unrestricted"] = (
        "workspace_scoped"
    )
    secrets: Literal["explicit_allowlist", "provider_managed", "unrestricted"] = (
        "explicit_allowlist"
    )


@dataclass(frozen=True)
class RunConstraints:
    max_steps: int | None = None
    timeout_seconds: int | None = None
    max_cost_usd: float | None = None

    def __post_init__(self) -> None:
        if self.max_steps is not None and self.max_steps <= 0:
            raise ValueError("max_steps must be positive")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_cost_usd is not None and self.max_cost_usd < 0:
            raise ValueError("max_cost_usd must be non-negative")


@dataclass(frozen=True)
class RunTarget:
    workspace: WorkspaceRef
    executor: ExecutorRef
    isolation: IsolationPolicy
    constraints: RunConstraints = field(default_factory=RunConstraints)
    annotations: Mapping[str, str] = field(default_factory=_empty_annotations)

    def __post_init__(self) -> None:
        object.__setattr__(self, "annotations", _copy_annotations(self.annotations))


class ExecutionBindingRunTargetError(ValueError):
    """Raised when a compatibility execution binding cannot map to RunTarget."""


def run_target_from_execution_binding(binding: ExecutionBinding) -> RunTarget:
    if isinstance(binding, LocalExecutionBinding):
        return RunTarget(
            workspace=LocalPathWorkspaceRef(
                path=binding.workspace_root,
                workspace_provider=binding.workspace_provider,
                provider_instance_id=binding.provider_instance_id,
            ),
            executor=LocalDaemonExecutorRef(),
            isolation=IsolationPolicy(kind="default_local_sandbox"),
        )

    if isinstance(binding, CloudWorkspaceBinding):
        return RunTarget(
            workspace=CloudWorkspaceRef(
                workspace_url=binding.workspace_url,
                workspace_id=binding.workspace_id,
                runtime_profile=binding.runtime_profile,
                workspace_provider=binding.workspace_provider,
                provider_instance_id=binding.provider_instance_id,
            ),
            executor=ManagedPoolExecutorRef(),
            isolation=IsolationPolicy(
                kind="provider_sandbox",
                network="provider_managed",
                filesystem="provider_managed",
                secrets="provider_managed",
            ),
        )

    if isinstance(binding, LocalAttachedExecutionBinding):
        return RunTarget(
            workspace=ExternalWorkerWorkspaceRef(
                ref={} if binding.workspace_ref is None else binding.workspace_ref,
                provider_instance_id=binding.provider_instance_id,
            ),
            executor=LocalAttachedExecutorRef(
                executor_kind=binding.executor_kind,
                worker_pool=binding.worker_pool,
            ),
            isolation=IsolationPolicy(kind="external_worker_policy"),
        )

    if isinstance(binding, ExternalWorkerBinding):
        return RunTarget(
            workspace=ExternalWorkerWorkspaceRef(
                ref={} if binding.workspace_ref is None else binding.workspace_ref,
                provider_instance_id=binding.provider_instance_id,
            ),
            executor=ExternalWorkerExecutorRef(
                executor_kind=binding.executor_kind,
                worker_pool=binding.worker_pool,
            ),
            isolation=IsolationPolicy(kind="external_worker_policy"),
        )

    raise ExecutionBindingRunTargetError(
        f"unsupported execution binding type: {type(binding).__name__}"
    )


__all__ = [
    "CloudWorkspaceRef",
    "ExecutionBindingRunTargetError",
    "ExecutorRef",
    "ExternalWorkerExecutorRef",
    "ExternalWorkerWorkspaceRef",
    "InlineExecutorRef",
    "IsolationPolicy",
    "LocalAttachedExecutorRef",
    "LocalDaemonExecutorRef",
    "LocalPathWorkspaceRef",
    "ManagedPoolExecutorRef",
    "RunConstraints",
    "RunTarget",
    "SnapshotWorkspaceRef",
    "WorkspaceRef",
    "run_target_from_execution_binding",
]
