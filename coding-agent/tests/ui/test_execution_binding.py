from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.ui.binding_resolver import (
    CloudBindingNotImplementedError,
    DefaultBindingResolver,
)
from coding_agent.ui.execution_binding import (
    CloudWorkspaceBinding,
    ExecutionBinding,
    LocalExecutionBinding,
)


def test_local_binding_round_trip(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    binding = LocalExecutionBinding(workspace_root=str(workspace))

    restored = ExecutionBinding.from_dict(binding.to_dict())

    assert isinstance(restored, LocalExecutionBinding)
    assert restored.workspace_root == str(workspace)


def test_cloud_binding_round_trip() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="https://workspace.example.com",
        workspace_id="ws-123",
    )

    restored = ExecutionBinding.from_dict(binding.to_dict())

    assert isinstance(restored, CloudWorkspaceBinding)
    assert restored.workspace_url == "https://workspace.example.com"
    assert restored.workspace_id == "ws-123"


def test_unknown_binding_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown binding kind"):
        ExecutionBinding.from_dict({"kind": "unknown"})


def test_local_binding_requires_string_workspace_root() -> None:
    with pytest.raises(TypeError, match="string workspace_root"):
        LocalExecutionBinding.from_dict({"kind": "local", "workspace_root": 123})


def test_local_resolver_returns_absolute_path(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    binding = LocalExecutionBinding(workspace_root=str(workspace))
    resolver = DefaultBindingResolver()

    resolved = resolver.resolve_workspace_root(binding)
    assert resolved == workspace.resolve()
    assert resolver.resolve_tool_config(binding) == {"workspace_root": str(resolved)}


def test_cloud_resolver_raises_typed_not_implemented() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="https://workspace.example.com",
        workspace_id="ws-123",
    )
    resolver = DefaultBindingResolver()

    with pytest.raises(CloudBindingNotImplementedError, match="cloud workspace"):
        resolver.resolve_workspace_root(binding)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"kind": "cloud", "workspace_url": 123, "workspace_id": "ws-123"},
            "string workspace_url and workspace_id",
        ),
        (
            {
                "kind": "cloud",
                "workspace_url": "https://workspace.example.com",
                "workspace_id": 123,
            },
            "string workspace_url and workspace_id",
        ),
    ],
)
def test_cloud_binding_rejects_invalid_field_types(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(TypeError, match=message):
        CloudWorkspaceBinding.from_dict(payload)


def test_cloud_resolver_tool_config_raises_typed_not_implemented() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="https://workspace.example.com",
        workspace_id="ws-123",
    )
    resolver = DefaultBindingResolver()

    with pytest.raises(CloudBindingNotImplementedError, match="cloud workspace"):
        resolver.resolve_tool_config(binding)
