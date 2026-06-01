from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from coding_agent.server.binding_resolver import (
    CloudBindingNotImplementedError,
    DefaultBindingResolver,
)
from coding_agent.environment import (
    CloudCommandResult,
    CloudEnvironment,
    LocalEnvironment,
)
from coding_agent.environment import workspace_provider as workspace_provider_module
from coding_agent.environment.workspace_provider import (
    CloudWorkspaceSource,
    cloud_client_factory_from_config,
    cloud_workspace_ready_from_config,
    provision_cloud_binding_from_config,
    register_workspace_provider,
    workspace_provider_capabilities_from_config,
    WorkspaceProvider,
)
from coding_agent.server.execution_binding import (
    CloudWorkspaceBinding,
    ExecutionBinding,
    LocalAttachedExecutionBinding,
    LocalExecutionBinding,
)


class FakeCloudClient:
    workspace_url: str = "https://workspace.example.com"
    workspace_id: str = "ws-123"
    default_cwd: str = "/workspace"

    def read_file(self, path: str) -> str:
        return f"read:{path}"

    def write_file(self, path: str, content: str) -> None:
        del path, content

    def replace_file(self, path: str, old: str, new: str) -> None:
        del path, old, new

    def glob_files(self, pattern: str, directory: str) -> list[str]:
        del pattern, directory
        return []

    def grep_search(self, pattern: str, directory: str, include: str) -> list[str]:
        del pattern, directory, include
        return []

    def apply_patch(self, path: str, patch: str) -> dict[str, object]:
        del patch
        return {"success": True, "path": path, "changed": False}

    def run_command(
        self,
        command: str,
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        timeout: int,
    ) -> CloudCommandResult:
        del command, cwd, env, timeout
        return CloudCommandResult(stdout="", stderr="", exit_code=0)


def test_local_binding_round_trip(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    binding = LocalExecutionBinding(workspace_root=str(workspace))

    restored = ExecutionBinding.from_dict(binding.to_dict())

    assert isinstance(restored, LocalExecutionBinding)
    assert restored.workspace_root == str(workspace)


def test_local_binding_round_trips_explicit_workspace_provider_metadata(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    binding = LocalExecutionBinding(
        workspace_root=str(workspace),
        workspace_provider="local",
        provider_instance_id="local-dev",
    )

    restored = ExecutionBinding.from_dict(binding.to_dict())

    assert isinstance(restored, LocalExecutionBinding)
    assert restored.workspace_root == str(workspace)
    assert restored.workspace_provider == "local"
    assert restored.provider_instance_id == "local-dev"


def test_cloud_binding_round_trip() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="https://workspace.example.com",
        workspace_id="ws-123",
    )

    restored = ExecutionBinding.from_dict(binding.to_dict())

    assert isinstance(restored, CloudWorkspaceBinding)
    assert restored.workspace_url == "https://workspace.example.com"
    assert restored.workspace_id == "ws-123"


def test_cloud_binding_round_trips_explicit_workspace_provider_metadata() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="https://workspace.example.com",
        workspace_id="ws-123",
        workspace_provider="docker",
        provider_instance_id="docker-host-a",
    )

    restored = ExecutionBinding.from_dict(binding.to_dict())

    assert isinstance(restored, CloudWorkspaceBinding)
    assert restored.workspace_provider == "docker"
    assert restored.provider_instance_id == "docker-host-a"


def test_cloud_binding_round_trip_preserves_runtime_profile() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="https://workspace.example.com",
        workspace_id="ws-123",
        runtime_profile="universal",
    )

    restored = ExecutionBinding.from_dict(binding.to_dict())

    assert isinstance(restored, CloudWorkspaceBinding)
    assert restored.runtime_profile == "universal"


def test_local_attached_binding_round_trip() -> None:
    binding = LocalAttachedExecutionBinding(
        executor_kind="local_cli",
        worker_pool="default",
        workspace_ref={"path": "/repo"},
        provider_instance_id="macbook",
    )

    restored = ExecutionBinding.from_dict(binding.to_dict())

    assert isinstance(restored, LocalAttachedExecutionBinding)
    assert restored.executor_kind == "local_cli"
    assert restored.worker_pool == "default"
    assert restored.workspace_ref == {"path": "/repo"}
    assert restored.provider_instance_id == "macbook"
    assert restored.to_dict()["kind"] == "local_attached"


def test_unknown_binding_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown binding kind"):
        _ = ExecutionBinding.from_dict({"kind": "unknown"})


def test_local_binding_requires_string_workspace_root() -> None:
    with pytest.raises(TypeError, match="string workspace_root"):
        _ = LocalExecutionBinding.from_dict({"kind": "local", "workspace_root": 123})


def test_local_binding_rejects_empty_workspace_root() -> None:
    with pytest.raises(ValueError, match="workspace_root must be non-empty"):
        _ = LocalExecutionBinding.from_dict({"kind": "local", "workspace_root": " "})


def test_local_resolver_returns_absolute_path(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    binding = LocalExecutionBinding(workspace_root=str(workspace))
    resolver = DefaultBindingResolver()

    resolved = resolver.resolve_workspace_root(binding)
    assert resolved == workspace.resolve()
    assert resolver.resolve_tool_config(binding) == {"workspace_root": str(resolved)}


def test_local_resolver_returns_local_environment(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    binding = LocalExecutionBinding(workspace_root=str(workspace))
    resolver = DefaultBindingResolver()

    env = resolver.resolve_environment(binding)

    assert isinstance(env, LocalEnvironment)
    assert env.workspace_root == workspace.resolve()


def test_cloud_resolver_raises_typed_not_implemented() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="https://workspace.example.com",
        workspace_id="ws-123",
    )
    resolver = DefaultBindingResolver()

    with pytest.raises(CloudBindingNotImplementedError, match="cloud workspace"):
        _ = resolver.resolve_workspace_root(binding)


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
        (
            {
                "kind": "cloud",
                "workspace_url": " ",
                "workspace_id": "ws-123",
            },
            "workspace_url must be non-empty",
        ),
        (
            {
                "kind": "cloud",
                "workspace_url": "https://workspace.example.com",
                "workspace_id": " ",
            },
            "workspace_id must be non-empty",
        ),
        (
            {
                "kind": "cloud",
                "workspace_url": "https://workspace.example.com",
                "workspace_id": "ws-123",
                "runtime_profile": " ",
            },
            "runtime_profile must be non-empty",
        ),
    ],
)
def test_cloud_binding_rejects_invalid_field_types(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        _ = CloudWorkspaceBinding.from_dict(payload)


def test_cloud_resolver_tool_config_raises_typed_not_implemented() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="https://workspace.example.com",
        workspace_id="ws-123",
    )
    resolver = DefaultBindingResolver()

    with pytest.raises(CloudBindingNotImplementedError, match="cloud workspace"):
        _ = resolver.resolve_tool_config(binding)


def test_cloud_resolver_environment_raises_typed_not_implemented() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="https://workspace.example.com",
        workspace_id="ws-123",
    )
    resolver = DefaultBindingResolver()

    with pytest.raises(CloudBindingNotImplementedError, match="cloud workspace"):
        _ = resolver.resolve_environment(binding)


def test_cloud_binding_resolves_to_cloud_environment_when_client_available() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="https://workspace.example.com",
        workspace_id="ws-123",
    )
    captured: list[CloudWorkspaceBinding] = []

    def build_client(resolved_binding: CloudWorkspaceBinding) -> FakeCloudClient:
        captured.append(resolved_binding)
        return FakeCloudClient()

    resolver = DefaultBindingResolver(cloud_client_factory=build_client)

    env = resolver.resolve_environment(binding)

    assert isinstance(env, CloudEnvironment)
    assert captured == [binding]
    assert env.tool_config() == {
        "workspace_id": "ws-123",
        "workspace_url": "https://workspace.example.com",
    }


class FakeWorkspaceProvider:
    def __init__(self) -> None:
        self.seen_configs: list[dict[str, object]] = []

    def build_cloud_client_factory(
        self, config: dict[str, object]
    ) -> Callable[[CloudWorkspaceBinding], FakeCloudClient]:
        self.seen_configs.append(dict(config))

        def build_client(binding: CloudWorkspaceBinding) -> FakeCloudClient:
            assert binding.workspace_url == "https://workspace.example.com"
            assert binding.workspace_id == "ws-123"
            return FakeCloudClient()

        return build_client

    def provision_cloud_workspace_binding(
        self,
        config: dict[str, object],
        source: CloudWorkspaceSource,
    ) -> CloudWorkspaceBinding:
        self.seen_configs.append(dict(config))
        assert source == {"kind": "docker"}
        return CloudWorkspaceBinding(
            workspace_url="https://workspace.example.com",
            workspace_id="ws-123",
        )

    def cleanup_cloud_workspace_binding(
        self,
        config: dict[str, object],
        binding: CloudWorkspaceBinding,
    ) -> None:
        self.seen_configs.append(dict(config))
        assert binding.workspace_id == "ws-123"

    def import_workspace_archive(
        self,
        config: dict[str, object],
        binding: CloudWorkspaceBinding,
        archive_base64: str,
    ) -> None:
        self.seen_configs.append(dict(config))
        assert binding.workspace_id == "ws-123"
        assert archive_base64 == "archive"

    def export_workspace_archive(
        self,
        config: dict[str, object],
        binding: CloudWorkspaceBinding,
    ) -> str:
        self.seen_configs.append(dict(config))
        assert binding.workspace_id == "ws-123"
        return "archive"

    def check_readiness(self, config: dict[str, object]) -> bool:
        self.seen_configs.append(dict(config))
        return True


def test_cloud_workspace_provider_registry_builds_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_provider_module, "_WORKSPACE_PROVIDERS", {})
    provider = FakeWorkspaceProvider()
    register_workspace_provider(
        "fake-provider", cast(WorkspaceProvider, cast(object, provider))
    )

    factory = cloud_client_factory_from_config(
        {
            "provider": "fake-provider",
            "workspace_root": "/srv/workspaces",
        }
    )

    client = factory(
        CloudWorkspaceBinding(
            workspace_url="https://workspace.example.com",
            workspace_id="ws-123",
        )
    )

    assert isinstance(client, FakeCloudClient)
    assert provider.seen_configs == [
        {"provider": "fake-provider", "workspace_root": "/srv/workspaces"}
    ]


def test_cloud_workspace_provider_config_requires_provider() -> None:
    with pytest.raises(
        ValueError,
        match=r"cloud_workspace\.provider is required when cloud_workspace\.enabled=true",
    ):
        _ = cloud_client_factory_from_config({"enabled": True})


def test_workspace_provider_registry_provisions_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_provider_module, "_WORKSPACE_PROVIDERS", {})
    provider = FakeWorkspaceProvider()
    register_workspace_provider(
        "fake-provider", cast(WorkspaceProvider, cast(object, provider))
    )

    binding = provision_cloud_binding_from_config(
        {
            "provider": "fake-provider",
            "workspace_root": "/srv/workspaces",
        },
        {"kind": "docker"},
    )

    assert isinstance(binding, CloudWorkspaceBinding)
    assert binding.workspace_id == "ws-123"


def test_workspace_provider_registry_checks_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_provider_module, "_WORKSPACE_PROVIDERS", {})
    provider = FakeWorkspaceProvider()
    register_workspace_provider(
        "fake-provider", cast(WorkspaceProvider, cast(object, provider))
    )

    ready = cloud_workspace_ready_from_config(
        {
            "provider": "fake-provider",
            "workspace_root": "/srv/workspaces",
        }
    )

    assert ready is True
    assert provider.seen_configs == [
        {"provider": "fake-provider", "workspace_root": "/srv/workspaces"}
    ]


def test_workspace_provider_capabilities_fall_back_to_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_provider_module, "_WORKSPACE_PROVIDERS", {})
    provider = FakeWorkspaceProvider()
    register_workspace_provider(
        "fake-provider", cast(WorkspaceProvider, cast(object, provider))
    )

    capabilities = workspace_provider_capabilities_from_config(
        {
            "provider": "fake-provider",
            "workspace_root": "/srv/workspaces",
        }
    )

    assert capabilities.provider == "fake-provider"
    assert capabilities.available is True
    assert capabilities.reason == "ready"
    assert capabilities.supports_provision is True
    assert capabilities.supports_archive is True
    assert capabilities.supports_diff is True
    assert capabilities.supports_patch is True
    assert capabilities.supports_publish is True
