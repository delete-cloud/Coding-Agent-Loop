from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Literal


def _optional_metadata_str(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"binding {key} must be a string")
    if not value.strip():
        raise ValueError(f"binding {key} must be non-empty")
    return value


@dataclass(frozen=True)
class ExecutionBinding:
    kind: ClassVar[Literal["local", "cloud", "external_worker"]]

    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionBinding:
        kind = data.get("kind")
        if kind == "local":
            return LocalExecutionBinding.from_dict(data)
        if kind == "cloud":
            return CloudWorkspaceBinding.from_dict(data)
        if kind == "external_worker":
            return ExternalWorkerBinding.from_dict(data)
        raise ValueError(f"unknown binding kind: {kind}")


@dataclass(frozen=True)
class LocalExecutionBinding(ExecutionBinding):
    workspace_root: str
    workspace_provider: str | None = None
    provider_instance_id: str | None = None
    kind: ClassVar[Literal["local"]] = "local"

    def to_dict(self) -> dict[str, Any]:
        payload = {"kind": self.kind, "workspace_root": self.workspace_root}
        if self.workspace_provider is not None:
            payload["workspace_provider"] = self.workspace_provider
        if self.provider_instance_id is not None:
            payload["provider_instance_id"] = self.provider_instance_id
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LocalExecutionBinding:
        root = data.get("workspace_root")
        if not isinstance(root, str):
            raise TypeError("local binding requires string workspace_root")
        if not root.strip():
            raise ValueError("local binding workspace_root must be non-empty")
        return cls(
            workspace_root=root,
            workspace_provider=_optional_metadata_str(data, "workspace_provider"),
            provider_instance_id=_optional_metadata_str(data, "provider_instance_id"),
        )


@dataclass(frozen=True)
class CloudWorkspaceBinding(ExecutionBinding):
    workspace_url: str
    workspace_id: str
    runtime_profile: str | None = None
    workspace_provider: str | None = None
    provider_instance_id: str | None = None
    kind: ClassVar[Literal["cloud"]] = "cloud"

    def to_dict(self) -> dict[str, Any]:
        payload = {
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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CloudWorkspaceBinding:
        workspace_url = data.get("workspace_url")
        workspace_id = data.get("workspace_id")
        runtime_profile = data.get("runtime_profile")
        if not isinstance(workspace_url, str) or not isinstance(workspace_id, str):
            raise TypeError(
                "cloud binding requires string workspace_url and workspace_id"
            )
        if not workspace_url.strip():
            raise ValueError("cloud binding workspace_url must be non-empty")
        if not workspace_id.strip():
            raise ValueError("cloud binding workspace_id must be non-empty")
        if runtime_profile is not None and not isinstance(runtime_profile, str):
            raise TypeError("cloud binding runtime_profile must be a string")
        if isinstance(runtime_profile, str) and not runtime_profile.strip():
            raise ValueError("cloud binding runtime_profile must be non-empty")
        return cls(
            workspace_url=workspace_url,
            workspace_id=workspace_id,
            runtime_profile=runtime_profile,
            workspace_provider=_optional_metadata_str(data, "workspace_provider"),
            provider_instance_id=_optional_metadata_str(data, "provider_instance_id"),
        )


@dataclass(frozen=True)
class ExternalWorkerBinding(ExecutionBinding):
    executor_kind: str
    worker_pool: str = "default"
    workspace_ref: dict[str, Any] | None = None
    provider_instance_id: str | None = None
    kind: ClassVar[Literal["external_worker"]] = "external_worker"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "executor_kind": self.executor_kind,
            "worker_pool": self.worker_pool,
        }
        if self.workspace_ref is not None:
            payload["workspace_ref"] = dict(self.workspace_ref)
        if self.provider_instance_id is not None:
            payload["provider_instance_id"] = self.provider_instance_id
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExternalWorkerBinding:
        executor_kind = data.get("executor_kind")
        worker_pool = data.get("worker_pool", "default")
        workspace_ref = data.get("workspace_ref")
        if not isinstance(executor_kind, str):
            raise TypeError("external_worker binding requires string executor_kind")
        if not executor_kind.strip():
            raise ValueError("external_worker binding executor_kind must be non-empty")
        if not isinstance(worker_pool, str):
            raise TypeError("external_worker binding worker_pool must be a string")
        if not worker_pool.strip():
            raise ValueError("external_worker binding worker_pool must be non-empty")
        if workspace_ref is not None and not isinstance(workspace_ref, dict):
            raise TypeError("external_worker binding workspace_ref must be an object")
        return cls(
            executor_kind=executor_kind,
            worker_pool=worker_pool,
            workspace_ref=None if workspace_ref is None else dict(workspace_ref),
            provider_instance_id=_optional_metadata_str(data, "provider_instance_id"),
        )
