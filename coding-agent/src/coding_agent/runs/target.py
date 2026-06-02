from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar, Literal, TypeAlias, cast

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


def _require_mapping(data: object, *, field_name: str) -> Mapping[str, object]:
    if not isinstance(data, Mapping):
        raise TypeError(f"{field_name} must be an object")
    return cast(Mapping[str, object], data)


def _require_str(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    _require_non_empty(value, field_name=key)
    return value


def _optional_str(data: Mapping[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    _require_non_empty(value, field_name=key)
    return value


def _optional_int(data: Mapping[str, object], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    return value


def _optional_float(data: Mapping[str, object], key: str) -> float | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, int | float):
        raise TypeError(f"{key} must be a number")
    return float(value)


def _optional_str_with_default(
    data: Mapping[str, object],
    key: str,
    default: str,
) -> str:
    value = _optional_str(data, key)
    return default if value is None else value


def _literal_value(
    data: Mapping[str, object],
    key: str,
    allowed: set[str],
    default: str | None = None,
) -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    if value not in allowed:
        raise ValueError(f"{key} has unsupported value: {value}")
    return value


def _optional_metadata(
    data: Mapping[str, object],
    key: str,
) -> Mapping[str, object] | None:
    value = data.get(key)
    if value is None:
        return None
    return _require_mapping(value, field_name=key)


@dataclass(frozen=True)
class LocalPathWorkspaceRef:
    path: str
    workspace_provider: str | None = None
    provider_instance_id: str | None = None
    kind: ClassVar[Literal["local_path"]] = "local_path"

    def __post_init__(self) -> None:
        _require_non_empty(self.path, field_name="workspace path")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"kind": self.kind, "path": self.path}
        if self.workspace_provider is not None:
            payload["workspace_provider"] = self.workspace_provider
        if self.provider_instance_id is not None:
            payload["provider_instance_id"] = self.provider_instance_id
        return payload


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

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "kind": self.kind,
            "workspace_url": self.workspace_url,
            "workspace_id": self.workspace_id,
        }
        if self.runtime_profile is not None:
            payload["runtime_profile"] = self.runtime_profile
        if self.workspace_provider is not None:
            payload["workspace_provider"] = self.workspace_provider
        if self.provider_instance_id is not None:
            payload["provider_instance_id"] = self.provider_instance_id
        return payload


@dataclass(frozen=True)
class ExternalWorkerWorkspaceRef:
    ref: Mapping[str, object] = field(default_factory=_empty_metadata)
    provider_instance_id: str | None = None
    kind: ClassVar[Literal["external_worker_ref"]] = "external_worker_ref"

    def __post_init__(self) -> None:
        object.__setattr__(self, "ref", _copy_metadata(self.ref))

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"kind": self.kind, "ref": dict(self.ref)}
        if self.provider_instance_id is not None:
            payload["provider_instance_id"] = self.provider_instance_id
        return payload


@dataclass(frozen=True)
class SnapshotWorkspaceRef:
    snapshot_id: str
    kind: ClassVar[Literal["snapshot"]] = "snapshot"

    def __post_init__(self) -> None:
        _require_non_empty(self.snapshot_id, field_name="snapshot_id")

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "snapshot_id": self.snapshot_id}


WorkspaceRef: TypeAlias = (
    LocalPathWorkspaceRef
    | CloudWorkspaceRef
    | ExternalWorkerWorkspaceRef
    | SnapshotWorkspaceRef
)


@dataclass(frozen=True)
class LocalDaemonExecutorRef:
    kind: ClassVar[Literal["local_daemon"]] = "local_daemon"

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind}


@dataclass(frozen=True)
class ManagedPoolExecutorRef:
    pool: str = "default"
    kind: ClassVar[Literal["managed_pool"]] = "managed_pool"

    def __post_init__(self) -> None:
        _require_non_empty(self.pool, field_name="managed executor pool")

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "pool": self.pool}


@dataclass(frozen=True)
class ExternalWorkerExecutorRef:
    executor_kind: str
    worker_pool: str = "default"
    kind: ClassVar[Literal["external_worker"]] = "external_worker"

    def __post_init__(self) -> None:
        _require_non_empty(self.executor_kind, field_name="executor_kind")
        _require_non_empty(self.worker_pool, field_name="worker_pool")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "executor_kind": self.executor_kind,
            "worker_pool": self.worker_pool,
        }


@dataclass(frozen=True)
class LocalAttachedExecutorRef:
    executor_kind: str
    worker_pool: str = "default"
    kind: ClassVar[Literal["local_attached"]] = "local_attached"

    def __post_init__(self) -> None:
        _require_non_empty(self.executor_kind, field_name="executor_kind")
        _require_non_empty(self.worker_pool, field_name="worker_pool")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "executor_kind": self.executor_kind,
            "worker_pool": self.worker_pool,
        }


@dataclass(frozen=True)
class InlineExecutorRef:
    kind: ClassVar[Literal["inline_testkit"]] = "inline_testkit"

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind}


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

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "network": self.network,
            "filesystem": self.filesystem,
            "secrets": self.secrets,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> IsolationPolicy:
        return cls(
            kind=cast(
                Literal[
                    "default_local_sandbox",
                    "provider_sandbox",
                    "external_worker_policy",
                    "dev_unsafe_disabled",
                ],
                _literal_value(
                    data,
                    "kind",
                    {
                        "default_local_sandbox",
                        "provider_sandbox",
                        "external_worker_policy",
                        "dev_unsafe_disabled",
                    },
                ),
            ),
            network=cast(
                Literal["restricted", "provider_managed", "unrestricted"],
                _literal_value(
                    data,
                    "network",
                    {"restricted", "provider_managed", "unrestricted"},
                    default="restricted",
                ),
            ),
            filesystem=cast(
                Literal["workspace_scoped", "provider_managed", "unrestricted"],
                _literal_value(
                    data,
                    "filesystem",
                    {"workspace_scoped", "provider_managed", "unrestricted"},
                    default="workspace_scoped",
                ),
            ),
            secrets=cast(
                Literal[
                    "explicit_allowlist",
                    "provider_managed",
                    "unrestricted",
                ],
                _literal_value(
                    data,
                    "secrets",
                    {
                        "explicit_allowlist",
                        "provider_managed",
                        "unrestricted",
                    },
                    default="explicit_allowlist",
                ),
            ),
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

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {}
        if self.max_steps is not None:
            payload["max_steps"] = self.max_steps
        if self.timeout_seconds is not None:
            payload["timeout_seconds"] = self.timeout_seconds
        if self.max_cost_usd is not None:
            payload["max_cost_usd"] = self.max_cost_usd
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> RunConstraints:
        return cls(
            max_steps=_optional_int(data, "max_steps"),
            timeout_seconds=_optional_int(data, "timeout_seconds"),
            max_cost_usd=_optional_float(data, "max_cost_usd"),
        )


@dataclass(frozen=True)
class RunTarget:
    workspace: WorkspaceRef
    executor: ExecutorRef
    isolation: IsolationPolicy
    constraints: RunConstraints = field(default_factory=RunConstraints)
    annotations: Mapping[str, str] = field(default_factory=_empty_annotations)

    def __post_init__(self) -> None:
        object.__setattr__(self, "annotations", _copy_annotations(self.annotations))

    def to_dict(self) -> dict[str, object]:
        return {
            "workspace": workspace_ref_to_dict(self.workspace),
            "executor": executor_ref_to_dict(self.executor),
            "isolation": self.isolation.to_dict(),
            "constraints": self.constraints.to_dict(),
            "annotations": dict(self.annotations),
        }


class ExecutionBindingRunTargetError(ValueError):
    """Raised when a compatibility execution binding cannot map to RunTarget."""


class RunTargetSerializationError(ValueError):
    """Raised when serialized RunTarget metadata is invalid."""


def workspace_ref_to_dict(ref: WorkspaceRef) -> dict[str, object]:
    return ref.to_dict()


def workspace_ref_from_dict(data: Mapping[str, object]) -> WorkspaceRef:
    kind = data.get("kind")
    if kind == "local_path":
        return LocalPathWorkspaceRef(
            path=_require_str(data, "path"),
            workspace_provider=_optional_str(data, "workspace_provider"),
            provider_instance_id=_optional_str(data, "provider_instance_id"),
        )
    if kind == "cloud_workspace":
        return CloudWorkspaceRef(
            workspace_url=_require_str(data, "workspace_url"),
            workspace_id=_require_str(data, "workspace_id"),
            runtime_profile=_optional_str(data, "runtime_profile"),
            workspace_provider=_optional_str(data, "workspace_provider"),
            provider_instance_id=_optional_str(data, "provider_instance_id"),
        )
    if kind == "external_worker_ref":
        return ExternalWorkerWorkspaceRef(
            ref=_optional_metadata(data, "ref") or {},
            provider_instance_id=_optional_str(data, "provider_instance_id"),
        )
    if kind == "snapshot":
        return SnapshotWorkspaceRef(snapshot_id=_require_str(data, "snapshot_id"))
    raise RunTargetSerializationError(f"unknown workspace ref kind: {kind}")


def executor_ref_to_dict(ref: ExecutorRef) -> dict[str, object]:
    return ref.to_dict()


def executor_ref_from_dict(data: Mapping[str, object]) -> ExecutorRef:
    kind = data.get("kind")
    if kind == "local_daemon":
        return LocalDaemonExecutorRef()
    if kind == "managed_pool":
        return ManagedPoolExecutorRef(
            pool=_optional_str_with_default(data, "pool", "default")
        )
    if kind == "external_worker":
        return ExternalWorkerExecutorRef(
            executor_kind=_require_str(data, "executor_kind"),
            worker_pool=_optional_str_with_default(data, "worker_pool", "default"),
        )
    if kind == "local_attached":
        return LocalAttachedExecutorRef(
            executor_kind=_require_str(data, "executor_kind"),
            worker_pool=_optional_str_with_default(data, "worker_pool", "default"),
        )
    if kind == "inline_testkit":
        return InlineExecutorRef()
    raise RunTargetSerializationError(f"unknown executor ref kind: {kind}")


def run_target_from_dict(data: Mapping[str, object]) -> RunTarget:
    workspace_data = _require_mapping(data.get("workspace"), field_name="workspace")
    executor_data = _require_mapping(data.get("executor"), field_name="executor")
    isolation_data = _require_mapping(data.get("isolation"), field_name="isolation")
    constraints_data = _require_mapping(
        data.get("constraints", {}),
        field_name="constraints",
    )
    annotations_data = _require_mapping(
        data.get("annotations", {}),
        field_name="annotations",
    )
    annotations: dict[str, str] = {}
    for key, value in annotations_data.items():
        if not isinstance(value, str):
            raise TypeError(f"annotation value for {key} must be a string")
        annotations[key] = value
    return RunTarget(
        workspace=workspace_ref_from_dict(workspace_data),
        executor=executor_ref_from_dict(executor_data),
        isolation=IsolationPolicy.from_dict(isolation_data),
        constraints=RunConstraints.from_dict(constraints_data),
        annotations=annotations,
    )


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
    "RunTargetSerializationError",
    "SnapshotWorkspaceRef",
    "WorkspaceRef",
    "executor_ref_from_dict",
    "executor_ref_to_dict",
    "run_target_from_dict",
    "run_target_from_execution_binding",
    "workspace_ref_from_dict",
    "workspace_ref_to_dict",
]
