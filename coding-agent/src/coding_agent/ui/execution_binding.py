from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Literal


@dataclass(frozen=True)
class ExecutionBinding:
    kind: ClassVar[Literal["local", "cloud"]]

    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionBinding:
        kind = data.get("kind")
        if kind == "local":
            return LocalExecutionBinding.from_dict(data)
        if kind == "cloud":
            return CloudWorkspaceBinding.from_dict(data)
        raise ValueError(f"unknown binding kind: {kind}")


@dataclass(frozen=True)
class LocalExecutionBinding(ExecutionBinding):
    workspace_root: str
    kind: ClassVar[Literal["local"]] = "local"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "workspace_root": self.workspace_root}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LocalExecutionBinding:
        root = data.get("workspace_root")
        if not isinstance(root, str):
            raise TypeError("local binding requires string workspace_root")
        return cls(workspace_root=root)


@dataclass(frozen=True)
class CloudWorkspaceBinding(ExecutionBinding):
    workspace_url: str
    workspace_id: str
    kind: ClassVar[Literal["cloud"]] = "cloud"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "workspace_url": self.workspace_url,
            "workspace_id": self.workspace_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CloudWorkspaceBinding:
        workspace_url = data.get("workspace_url")
        workspace_id = data.get("workspace_id")
        if not isinstance(workspace_url, str) or not isinstance(workspace_id, str):
            raise TypeError(
                "cloud binding requires string workspace_url and workspace_id"
            )
        return cls(workspace_url=workspace_url, workspace_id=workspace_id)
