from __future__ import annotations

from pathlib import Path

from coding_agent.environment import (
    cleanup_cloud_binding_from_config,
    export_workspace_archive_from_config,
    import_workspace_archive_from_config,
    provision_cloud_binding_from_config,
)
from coding_agent.workspace_archive import (
    create_workspace_archive_base64,
    extract_workspace_archive_base64,
)


def test_docker_workspace_provider_imports_and_exports_workspace_archive(
    monkeypatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"

    monkeypatch.setattr(
        "coding_agent.environment.docker_workspace_provider._start_docker_workspace_container",
        lambda provider_config, binding: None,
    )

    config: dict[str, object] = {
        "provider": "docker",
        "workspace_root": str(workspace_root),
        "container_name_prefix": "agent-",
        "default_runtime_profile": "python-basic",
        "image_allowlist": ["python:3.11-slim"],
        "runtime_profiles": {
            "python-basic": {
                "provider": "docker",
                "image": "python:3.11-slim",
            }
        },
    }
    binding = provision_cloud_binding_from_config(config, {"kind": "docker"})

    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    (source / "README.md").write_text("uploaded\n", encoding="utf-8")
    (source / "nested" / "data.txt").write_text("from client\n", encoding="utf-8")

    import_workspace_archive_from_config(
        config,
        binding,
        create_workspace_archive_base64(source),
    )

    workspace_path = workspace_root / binding.workspace_id
    assert (workspace_path / "README.md").read_text(encoding="utf-8") == "uploaded\n"
    assert (workspace_path / "nested" / "data.txt").read_text(
        encoding="utf-8"
    ) == "from client\n"

    export_target = tmp_path / "export"
    extract_workspace_archive_base64(
        export_target,
        export_workspace_archive_from_config(config, binding),
    )

    assert (export_target / "README.md").read_text(encoding="utf-8") == "uploaded\n"
    assert (export_target / "nested" / "data.txt").read_text(
        encoding="utf-8"
    ) == "from client\n"

    cleanup_cloud_binding_from_config(config, binding)
