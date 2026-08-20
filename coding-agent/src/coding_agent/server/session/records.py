"""Durable SessionRecord schema and store-payload helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import (
    Any,
    cast,
)
from coding_agent.approval import ApprovalPolicy
from coding_agent.approval.store import ApprovalStore
from coding_agent.runs import (
    RunTarget,
    run_target_from_legacy_session_payload,
    run_target_from_dict,
)
from coding_agent.server.session.models import (
    _local_default_run_target,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from coding_agent.server.session.models import Session

logger = logging.getLogger("coding_agent.server.session_manager")


@dataclass(frozen=True)
class SessionRecord:
    """Durable session metadata stored across process restarts."""

    id: str
    created_at: datetime
    last_activity: datetime
    repo_path: Path | None
    origin: dict[str, str] | None
    default_run_target: RunTarget
    approval_policy: ApprovalPolicy
    provider_name: str | None
    model_name: str | None
    base_url: str | None
    max_steps: int
    mcp_servers: dict[str, dict[str, Any]]
    additional_directories: list[str]
    tape_id: str | None
    last_failure_details: str | None
    current_turn_id: str | None = None

    def to_store_data(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            # repo_path remains backward-compatible metadata and seeds the
            # default local RunTarget when placement metadata is omitted.
            "repo_path": None if self.repo_path is None else str(self.repo_path),
            "origin": None if self.origin is None else dict(self.origin),
            "default_run_target": self.default_run_target.to_dict(),
            "approval_policy": self.approval_policy.value,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "base_url": self.base_url,
            "max_steps": self.max_steps,
            "mcp_servers": dict(self.mcp_servers),
            "additional_directories": list(self.additional_directories),
            "tape_id": self.tape_id,
            "last_failure_details": self.last_failure_details,
            "turn_id": self.current_turn_id,
        }

    @classmethod
    def from_store_data(cls, data: dict[str, Any]) -> SessionRecord:
        repo_path_raw = data.get("repo_path")
        if repo_path_raw is not None and not isinstance(repo_path_raw, str):
            raise TypeError("session metadata has invalid repo_path")
        origin_raw = data.get("origin")
        if origin_raw is not None:
            if not isinstance(origin_raw, dict):
                raise TypeError("session metadata has invalid origin")
            origin = {
                key: _required_session_str(cast(dict[str, Any], origin_raw), key)
                for key in cast(dict[str, Any], origin_raw)
            }
        else:
            origin = None
        approval_policy_raw = data.get("approval_policy")
        if not isinstance(approval_policy_raw, str):
            raise TypeError("session metadata is missing approval_policy")
        provider_name_raw = data.get("provider_name")
        if provider_name_raw is not None and not isinstance(provider_name_raw, str):
            raise TypeError("session metadata has invalid provider_name")
        model_name_raw = data.get("model_name")
        if model_name_raw is not None and not isinstance(model_name_raw, str):
            raise TypeError("session metadata has invalid model_name")
        base_url_raw = data.get("base_url")
        if base_url_raw is not None and not isinstance(base_url_raw, str):
            raise TypeError("session metadata has invalid base_url")
        tape_id_raw = data.get("tape_id")
        if tape_id_raw is not None and not isinstance(tape_id_raw, str):
            raise TypeError("session metadata has invalid tape_id")
        last_failure_details_raw = data.get("last_failure_details")
        if last_failure_details_raw is not None and not isinstance(
            last_failure_details_raw, str
        ):
            raise TypeError("session metadata has invalid last_failure_details")
        current_turn_id_raw = data.get("turn_id")
        if current_turn_id_raw is not None and not isinstance(current_turn_id_raw, str):
            raise TypeError("session metadata has invalid turn_id")
        mcp_servers_raw = data.get("mcp_servers", {})
        if not isinstance(mcp_servers_raw, dict):
            raise TypeError("session metadata has invalid mcp_servers")
        mcp_servers = _session_mcp_servers_from_store(
            cast(dict[str, Any], mcp_servers_raw)
        )
        additional_directories = _session_additional_directories_from_store(
            data.get("additional_directories", [])
        )
        default_run_target_raw = data.get("default_run_target")
        if default_run_target_raw is None:
            legacy_target_raw = data.get("execution_binding")
            if legacy_target_raw is not None:
                if not isinstance(legacy_target_raw, dict):
                    raise TypeError("session metadata has invalid legacy run target")
                default_run_target = run_target_from_legacy_session_payload(
                    cast(dict[str, object], legacy_target_raw)
                )
            else:
                default_run_target = _local_default_run_target(
                    None if repo_path_raw is None else Path(repo_path_raw)
                )
        else:
            if not isinstance(default_run_target_raw, dict):
                raise TypeError("session metadata has invalid default_run_target")
            default_run_target = run_target_from_dict(default_run_target_raw)
        return cls(
            id=_required_session_str(data, "id"),
            created_at=datetime.fromisoformat(
                _required_session_str(data, "created_at")
            ),
            last_activity=datetime.fromisoformat(
                _required_session_str(data, "last_activity")
            ),
            repo_path=None if repo_path_raw is None else Path(repo_path_raw),
            origin=origin,
            default_run_target=default_run_target,
            approval_policy=ApprovalPolicy(approval_policy_raw),
            provider_name=provider_name_raw,
            model_name=model_name_raw,
            base_url=base_url_raw,
            max_steps=_required_session_int(data, "max_steps"),
            mcp_servers=mcp_servers,
            additional_directories=additional_directories,
            tape_id=tape_id_raw,
            last_failure_details=last_failure_details_raw,
            current_turn_id=current_turn_id_raw,
        )

    def to_session(self) -> Session:
        from coding_agent.server.session.models import Session

        return Session(
            id=self.id,
            created_at=self.created_at,
            last_activity=self.last_activity,
            approval_store=ApprovalStore(),
            repo_path=self.repo_path,
            origin=self.origin,
            default_run_target=self.default_run_target,
            approval_policy=self.approval_policy,
            provider_name=self.provider_name,
            model_name=self.model_name,
            base_url=self.base_url,
            max_steps=self.max_steps,
            mcp_servers=dict(self.mcp_servers),
            additional_directories=list(self.additional_directories),
            tape_id=self.tape_id,
            last_failure_details=self.last_failure_details,
            current_turn_id=self.current_turn_id,
        )


def _required_session_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise TypeError(f"session metadata is missing {key}")
    return value


def _required_session_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int):
        raise TypeError(f"session metadata is missing {key}")
    return value


def _session_mcp_servers_from_store(
    servers: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for name, raw in servers.items():
        if not isinstance(name, str) or not name:
            raise TypeError("session metadata has invalid mcp server name")
        if not isinstance(raw, dict):
            raise TypeError(f"session metadata has invalid mcp server: {name}")
        command = raw.get("command")
        if not isinstance(command, str) or not command:
            raise TypeError(f"session metadata has invalid mcp command: {name}")
        args = raw.get("args", [])
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise TypeError(f"session metadata has invalid mcp args: {name}")
        env = raw.get("env", {})
        if not isinstance(env, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in env.items()
        ):
            raise TypeError(f"session metadata has invalid mcp env: {name}")
        inherit_env = raw.get("inherit_env", False)
        if not isinstance(inherit_env, bool):
            raise TypeError(f"session metadata has invalid mcp inherit_env: {name}")
        normalized[name] = {
            "command": command,
            "args": list(args),
            "env": dict(env),
            "inherit_env": inherit_env,
        }
    return normalized


def _session_additional_directories_from_store(value: object) -> list[str]:
    if not isinstance(value, list):
        raise TypeError("session metadata has invalid additional_directories")
    normalized: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry:
            raise TypeError("session metadata has invalid additional_directories")
        path = Path(entry).expanduser()
        if not path.is_absolute():
            raise TypeError("session metadata has invalid additional_directories")
        normalized.append(str(path.resolve()))
    return normalized
