"""HTTP run-target, isolation, and session-origin helpers."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any


from coding_agent.server.auth import (
    AuthContext,
)
from coding_agent.runs import (
    CloudWorkspaceRef,
    IsolationPolicy,
    ManagedPoolExecutorRef,
    RunTarget,
    run_target_from_dict,
)
from coding_agent.server.schemas import (
    CreateSessionRequest,
)

from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


def _explicit_run_target_from_request(
    body: CreateSessionRequest | None,
    *,
    auth_context: AuthContext | None = None,
) -> RunTarget | None:
    if body is None:
        return None
    if body.default_run_target is not None and body.run_target is not None:
        raise ValueError("default_run_target and run_target cannot both be set")
    target_payload = (
        body.default_run_target
        if body.default_run_target is not None
        else body.run_target
    )
    if target_payload is None:
        return None
    return _lock_minimum_http_isolation(
        run_target_from_dict(target_payload),
        auth_context=auth_context,
    )


def _provisioned_run_target_from_request(
    body: CreateSessionRequest | None,
    *,
    auth_context: AuthContext | None = None,
) -> RunTarget | None:
    if body is None:
        return None
    explicit_target = _explicit_run_target_from_request(
        body,
        auth_context=auth_context,
    )
    if explicit_target is not None and body.workspace_source is not None:
        raise ValueError("run_target and workspace_source cannot be set together")
    if explicit_target is not None:
        return explicit_target
    if body.workspace_source is None:
        return None

    cloud_workspace_config = _bindings.module()._load_cloud_workspace_config()
    if cloud_workspace_config.get("enabled") is not True:
        raise ValueError(
            "cloud workspace provisioning requires cloud_workspace.enabled=true"
        )
    _validate_workspace_source_phase_policy(
        body.workspace_source.model_dump(mode="python"),
        cloud_workspace_config,
    )
    workspace = _bindings.module().provision_cloud_binding_from_config(
        cloud_workspace_config,
        body.workspace_source.model_dump(mode="python"),
    )
    return RunTarget(
        workspace=workspace,
        executor=ManagedPoolExecutorRef(),
        isolation=IsolationPolicy(
            kind="provider_sandbox",
            network="provider_managed",
            filesystem="provider_managed",
            secrets="provider_managed",
        ),
    )


def _can_disable_http_isolation(auth_context: AuthContext | None) -> bool:
    return auth_context is not None and auth_context.scope == "admin"


def _lock_minimum_http_isolation(
    target: RunTarget,
    *,
    auth_context: AuthContext | None,
) -> RunTarget:
    if _can_disable_http_isolation(auth_context):
        return target
    if target.isolation.kind != "dev_unsafe_disabled":
        return target
    return replace(
        target,
        isolation=IsolationPolicy(kind="default_local_sandbox"),
    )


def _validate_workspace_source_phase_policy(
    workspace_source: dict[str, object],
    cloud_workspace_config: dict[str, Any],
) -> None:
    setup_commands = workspace_source.get("setup_commands")
    if setup_commands is None:
        return
    remote_phases = cloud_workspace_config.get("remote_phases")
    setup_phase = (
        remote_phases.get("setup") if isinstance(remote_phases, dict) else None
    )
    allow_request_commands = (
        isinstance(setup_phase, dict)
        and setup_phase.get("allow_request_commands") is True
    )
    if not allow_request_commands:
        raise ValueError(
            "workspace_source.setup_commands requires "
            "remote_phases.setup.allow_request_commands=true"
        )
    raise ValueError("setup phase execution is not implemented yet")


def _session_origin_from_request(
    body: CreateSessionRequest | None,
    target: RunTarget | None,
    auth_context: AuthContext | None = None,
) -> dict[str, str]:
    origin = {
        "channel": "http",
        "placement_kind": "local_path" if target is None else target.workspace.kind,
        "executor_kind": "local_daemon" if target is None else target.executor.kind,
    }
    if body is not None and body.workspace_source is not None:
        origin["workspace_source_kind"] = body.workspace_source.kind
    if target is not None and isinstance(target.workspace, CloudWorkspaceRef):
        cloud_workspace_config = _bindings.module()._load_cloud_workspace_config()
        provider = cloud_workspace_config.get("provider")
        provider_instance_id = cloud_workspace_config.get("provider_instance_id")
        workspace_root_ref = cloud_workspace_config.get("workspace_root")
        workspace_host_label = cloud_workspace_config.get("workspace_host_label")
        if isinstance(provider, str) and provider.strip():
            origin["workspace_provider"] = provider.strip()
        if isinstance(provider_instance_id, str) and provider_instance_id.strip():
            origin["provider_instance_id"] = provider_instance_id.strip()
        if isinstance(workspace_root_ref, str) and workspace_root_ref.strip():
            origin["workspace_root_ref"] = workspace_root_ref.strip()
        if isinstance(workspace_host_label, str) and workspace_host_label.strip():
            origin["workspace_host_label"] = workspace_host_label.strip()
        elif isinstance(provider_instance_id, str) and provider_instance_id.strip():
            origin["workspace_host_label"] = provider_instance_id.strip()
    if auth_context is not None:
        origin["owner_label"] = auth_context.owner_label
        origin["auth_scope"] = auth_context.scope
    return origin


def _setup_phase_exception_detail(exc: BaseException) -> str | None:
    notes = [
        note
        for note in getattr(exc, "__notes__", ())
        if isinstance(note, str) and note.startswith("setup phase ")
    ]
    if not notes:
        return None

    returncode = getattr(exc, "returncode", None)
    if isinstance(returncode, int):
        prefix = f"setup phase failed with exit code {returncode}"
    else:
        prefix = "setup phase failed"
    return "\n".join([prefix, *notes])


def _http_exception_detail(exc: BaseException) -> str:
    setup_detail = _setup_phase_exception_detail(exc)
    if setup_detail is not None:
        return setup_detail
    return str(exc)


__all__ = [
    "_can_disable_http_isolation",
    "_explicit_run_target_from_request",
    "_http_exception_detail",
    "_lock_minimum_http_isolation",
    "_provisioned_run_target_from_request",
    "_session_origin_from_request",
    "_setup_phase_exception_detail",
    "_validate_workspace_source_phase_policy",
]
