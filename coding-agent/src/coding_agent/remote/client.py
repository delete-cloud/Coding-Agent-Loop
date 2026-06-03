"""Client helpers for remote Coding Agent HTTP sessions."""

from __future__ import annotations

import json
import os
import asyncio
import tempfile
from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import quote, urlencode

import click
import httpx
from httpx_sse import connect_sse

from coding_agent.remote.approval import APPROVAL_POLICIES
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    CompletionStatus,
    StreamDelta,
    ThinkingDelta,
    ToolCallDelta,
    ToolResultDelta,
    TurnEnd,
    TurnStatusDelta,
    WireMessage,
)


class DisplayWireConsumer(Protocol):
    async def emit(self, msg: WireMessage) -> None: ...
    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse: ...


@dataclass(frozen=True)
class RemoteEndpoint:
    name: str
    url: str
    token: str | None = None


def remotes_file_path() -> Path:
    override = os.environ.get("CODING_AGENT_REMOTES_FILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "coding-agent" / "remotes.json"


def load_remotes() -> dict[str, RemoteEndpoint]:
    path = remotes_file_path()
    if not path.exists():
        return {}
    try:
        raw = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid remotes file: {path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise click.ClickException(f"Invalid remotes file: {path}")
    raw = cast(Mapping[str, object], raw)
    remotes = raw.get("remotes")
    if not isinstance(remotes, Mapping):
        raise click.ClickException(f"Invalid remotes file: {path}")
    remotes = cast(Mapping[str, object], remotes)
    loaded: dict[str, RemoteEndpoint] = {}
    for name, value in remotes.items():
        if not isinstance(value, Mapping):
            raise click.ClickException(f"Invalid remote entry in {path}")
        value = cast(Mapping[str, object], value)
        url = value.get("url")
        token = value.get("token")
        if not isinstance(url, str) or not url.strip():
            raise click.ClickException(f"Remote {name} is missing url")
        if token is not None and not isinstance(token, str):
            raise click.ClickException(f"Remote {name} has invalid token")
        loaded[name] = RemoteEndpoint(name=name, url=url, token=token)
    return loaded


def save_remotes(remotes: dict[str, RemoteEndpoint]) -> None:
    path = remotes_file_path()
    parent_existed = path.parent.exists()
    _ = path.parent.mkdir(parents=True, exist_ok=True)
    if not parent_existed:
        _ = path.parent.chmod(0o700)
    payload = {
        "remotes": {
            name: _remote_payload(endpoint)
            for name, endpoint in sorted(remotes.items())
        }
    }
    contents = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            _ = temp_file.write(contents)
        _ = temp_path.chmod(0o600)
        _ = temp_path.replace(path)
        _ = path.chmod(0o600)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def add_remote(name: str, url: str, token: str | None) -> RemoteEndpoint:
    normalized_url = url.rstrip("/")
    if not normalized_url:
        raise click.ClickException("Remote URL must not be empty")
    remotes = load_remotes()
    endpoint = RemoteEndpoint(name=name, url=normalized_url, token=token)
    remotes[name] = endpoint
    save_remotes(remotes)
    return endpoint


def remove_remote(name: str) -> None:
    remotes = load_remotes()
    if name not in remotes:
        raise click.ClickException(f"Remote not found: {name}")
    del remotes[name]
    save_remotes(remotes)


def get_remote(name: str) -> RemoteEndpoint:
    remotes = load_remotes()
    try:
        return remotes[name]
    except KeyError as exc:
        raise click.ClickException(f"Remote not found: {name}") from exc


def auth_headers(endpoint: RemoteEndpoint) -> dict[str, str]:
    if endpoint.token is None:
        return {}
    return {"Authorization": f"Bearer {endpoint.token}"}


def create_remote_session(
    endpoint: RemoteEndpoint,
    *,
    snapshot_archive_base64: str | None = None,
    workspace_source: dict[str, object] | None = None,
    approval_policy: str = "auto",
    runtime_profile: str | None = None,
) -> str:
    if approval_policy not in APPROVAL_POLICIES:
        raise click.ClickException(f"Unsupported approval policy: {approval_policy}")
    if workspace_source is not None and snapshot_archive_base64 is not None:
        raise click.ClickException(
            "Pass either workspace_source or snapshot_archive_base64, not both."
        )
    if workspace_source is None:
        workspace_source = {"kind": "docker"}
        if snapshot_archive_base64 is not None:
            workspace_source["snapshot_archive_base64"] = snapshot_archive_base64
    else:
        workspace_source = dict(workspace_source)
    if runtime_profile is not None:
        workspace_source["runtime_profile"] = runtime_profile
    payload: dict[str, object] = {
        "workspace_source": workspace_source,
        "approval_policy": approval_policy,
    }
    try:
        with httpx.Client(
            base_url=endpoint.url,
            headers=auth_headers(endpoint),
            timeout=60.0,
        ) as client:
            response = client.post("/sessions", json=payload)
            _raise_remote_http_error(response, "create remote session")
            data = cast(object, response.json())
    except httpx.RequestError as exc:
        raise click.ClickException(f"Failed to create remote session: {exc}") from exc
    if not isinstance(data, Mapping):
        raise click.ClickException("Remote session response must be a JSON object")
    data = cast(Mapping[str, object], data)
    session_id = data.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise click.ClickException("Remote session response missing session_id")
    return session_id


def create_local_daemon_session(
    endpoint: RemoteEndpoint,
    *,
    repo_path: Path,
    approval_policy: str = "auto",
) -> str:
    if approval_policy not in APPROVAL_POLICIES:
        raise click.ClickException(f"Unsupported approval policy: {approval_policy}")
    payload: dict[str, object] = {
        "run_target": {
            "workspace": {
                "kind": "local_path",
                "path": str(repo_path.expanduser().resolve()),
            },
            "executor": {"kind": "local_daemon"},
            "isolation": {"kind": "default_local_sandbox"},
            "constraints": {},
            "annotations": {},
        },
        "approval_policy": approval_policy,
    }
    try:
        with httpx.Client(
            base_url=endpoint.url,
            headers=auth_headers(endpoint),
            timeout=60.0,
        ) as client:
            response = client.post("/sessions", json=payload)
            _raise_remote_http_error(response, "create local daemon session")
            data = cast(object, response.json())
    except httpx.RequestError as exc:
        raise click.ClickException(
            f"Failed to create local daemon session: {exc}"
        ) from exc
    if not isinstance(data, Mapping):
        raise click.ClickException("Local daemon session response must be a JSON object")
    data = cast(Mapping[str, object], data)
    session_id = data.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise click.ClickException("Local daemon session response missing session_id")
    return session_id


def list_remote_sessions(endpoint: RemoteEndpoint) -> list[dict[str, object]]:
    data = _get_remote_json(endpoint, "/sessions", "list remote sessions")
    sessions = data.get("sessions")
    if not isinstance(sessions, list):
        raise click.ClickException("Remote sessions response missing sessions")
    sessions = cast(list[object], sessions)
    return [dict(_expect_mapping(item, "Remote session entry")) for item in sessions]


def get_remote_session(endpoint: RemoteEndpoint, session_id: str) -> dict[str, object]:
    data = _get_remote_json(
        endpoint,
        f"/sessions/{session_id}",
        "get remote session",
    )
    return data


def list_remote_session_runs(
    endpoint: RemoteEndpoint,
    session_id: str,
) -> list[dict[str, object]]:
    data = _get_remote_json(
        endpoint,
        f"/sessions/{session_id}/runs",
        "list remote session runs",
    )
    runs = data.get("runs")
    if not isinstance(runs, list):
        raise click.ClickException("Remote session runs response missing runs")
    return [dict(_expect_mapping(item, "Remote run entry")) for item in runs]


def get_remote_run(endpoint: RemoteEndpoint, run_id: str) -> dict[str, object]:
    return _get_remote_json(endpoint, f"/runs/{run_id}", "get remote run")


def list_remote_run_events(
    endpoint: RemoteEndpoint,
    run_id: str,
) -> list[dict[str, object]]:
    data = _get_remote_json(
        endpoint,
        f"/runs/{run_id}/events",
        "list remote run events",
    )
    events = data.get("events")
    if not isinstance(events, list):
        raise click.ClickException("Remote run events response missing events")
    return [dict(_expect_mapping(item, "Remote run event entry")) for item in events]


def list_remote_run_display_events(
    endpoint: RemoteEndpoint,
    run_id: str,
) -> list[dict[str, object]]:
    data = _get_remote_json(
        endpoint,
        f"/runs/{run_id}/display-events",
        "list remote run display events",
    )
    events = data.get("events")
    if not isinstance(events, list):
        raise click.ClickException("Remote run display events response missing events")
    return [
        dict(_expect_mapping(item, "Remote run display event entry"))
        for item in events
    ]


def list_remote_run_interactions(
    endpoint: RemoteEndpoint,
    run_id: str,
    *,
    status: str | None = None,
) -> list[dict[str, object]]:
    path = f"/runs/{run_id}/interactions"
    if status is not None:
        path = f"{path}?status={quote(status)}"
    data = _get_remote_json(endpoint, path, "list remote run interactions")
    interactions = data.get("interactions")
    if not isinstance(interactions, list):
        raise click.ClickException("Remote interactions response missing interactions")
    return [
        dict(_expect_mapping(item, "Remote interaction entry")) for item in interactions
    ]


def list_remote_interactions(
    endpoint: RemoteEndpoint,
    *,
    session_id: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, object]]:
    params: list[tuple[str, str]] = []
    if session_id is not None:
        params.append(("session_id", session_id))
    if run_id is not None:
        params.append(("run_id", run_id))
    if status is not None:
        params.append(("status", status))
    path = "/interactions"
    if params:
        path = f"{path}?{urlencode(params)}"
    data = _get_remote_json(endpoint, path, "list remote interactions")
    interactions = data.get("interactions")
    if not isinstance(interactions, list):
        raise click.ClickException("Remote interactions response missing interactions")
    return [
        dict(_expect_mapping(item, "Remote interaction entry")) for item in interactions
    ]


def get_remote_interaction(
    endpoint: RemoteEndpoint,
    interaction_id: str,
) -> dict[str, object]:
    return _get_remote_json(
        endpoint,
        f"/interactions/{quote(interaction_id, safe='')}",
        "get remote interaction",
    )


def resolve_remote_interaction(
    endpoint: RemoteEndpoint,
    interaction_id: str,
    *,
    approved: bool,
    feedback: str | None = None,
    scope: str = "once",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "approved": approved,
        "scope": scope,
    }
    if feedback is not None:
        payload["feedback"] = feedback
    return _post_remote_json(
        endpoint,
        f"/interactions/{quote(interaction_id, safe='')}/resolve",
        json=payload,
        action="resolve remote interaction",
    )


def list_remote_workers(endpoint: RemoteEndpoint) -> list[dict[str, object]]:
    data = _get_remote_json(endpoint, "/workers", "list remote workers")
    workers = data.get("workers")
    if not isinstance(workers, list):
        raise click.ClickException("Remote workers response missing workers")
    return [dict(_expect_mapping(item, "Remote worker entry")) for item in workers]


def list_remote_executors(endpoint: RemoteEndpoint) -> list[dict[str, object]]:
    data = _get_remote_json(endpoint, "/executors", "list remote executors")
    executors = data.get("executors")
    if not isinstance(executors, list):
        raise click.ClickException("Remote executors response missing executors")
    return [dict(_expect_mapping(item, "Remote executor entry")) for item in executors]


def get_remote_worker(endpoint: RemoteEndpoint, worker_id: str) -> dict[str, object]:
    return _get_remote_json(
        endpoint,
        f"/workers/{worker_id}",
        "get remote worker",
    )


def get_remote_executor(
    endpoint: RemoteEndpoint, executor_id: str
) -> dict[str, object]:
    return _get_remote_json(
        endpoint,
        f"/executors/{executor_id}",
        "get remote executor",
    )


def get_remote_session_result(
    endpoint: RemoteEndpoint, session_id: str
) -> dict[str, object]:
    data = _get_remote_json(
        endpoint,
        f"/sessions/{session_id}/result",
        "get remote session result",
    )
    return data


def cancel_remote_session(
    endpoint: RemoteEndpoint, session_id: str
) -> dict[str, object]:
    data = _post_remote_json(
        endpoint,
        f"/sessions/{session_id}/cancel",
        "cancel remote session",
    )
    return dict(data)


def list_remote_workspaces(endpoint: RemoteEndpoint) -> list[dict[str, object]]:
    data = _get_remote_json(endpoint, "/workspaces", "list remote workspaces")
    workspaces = data.get("workspaces")
    if not isinstance(workspaces, list):
        raise click.ClickException("Remote workspaces response missing workspaces")
    workspaces = cast(list[object], workspaces)
    return [
        dict(_expect_mapping(item, "Remote workspace entry")) for item in workspaces
    ]


def get_remote_workspace(
    endpoint: RemoteEndpoint, workspace_id: str
) -> dict[str, object]:
    return _get_remote_json(
        endpoint,
        f"/workspaces/{workspace_id}",
        "get remote workspace",
    )


def retain_remote_workspace(
    endpoint: RemoteEndpoint,
    workspace_id: str,
    *,
    retention_policy: str,
    ttl_seconds: int | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"retention_policy": retention_policy}
    if ttl_seconds is not None:
        payload["ttl_seconds"] = ttl_seconds
    return _post_remote_json(
        endpoint,
        f"/workspaces/{workspace_id}/retain",
        "retain remote workspace",
        json=payload,
    )


def pin_remote_workspace(
    endpoint: RemoteEndpoint, workspace_id: str
) -> dict[str, object]:
    return _post_remote_json(
        endpoint,
        f"/workspaces/{workspace_id}/pin",
        "pin remote workspace",
    )


def unpin_remote_workspace(
    endpoint: RemoteEndpoint,
    workspace_id: str,
    *,
    retention_policy: str | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {}
    if retention_policy is not None:
        payload["retention_policy"] = retention_policy
    if ttl_seconds is not None:
        payload["ttl_seconds"] = ttl_seconds
    return _post_remote_json(
        endpoint,
        f"/workspaces/{workspace_id}/unpin",
        "unpin remote workspace",
        json=payload if payload else None,
    )


def cleanup_stale_remote_workspaces(endpoint: RemoteEndpoint) -> int:
    data = _post_remote_json(endpoint, "/workspaces/gc", "cleanup stale workspaces")
    cleaned_count = data.get("cleaned_count")
    if not isinstance(cleaned_count, int):
        raise click.ClickException("Remote workspace GC response missing cleaned_count")
    return cleaned_count


def cleanup_remote_workspace(
    endpoint: RemoteEndpoint, workspace_id: str
) -> dict[str, object]:
    try:
        with httpx.Client(
            base_url=endpoint.url,
            headers=auth_headers(endpoint),
            timeout=30.0,
        ) as client:
            response = client.delete(f"/workspaces/{workspace_id}")
            _raise_remote_http_error(response, "cleanup remote workspace")
            data = cast(object, response.json())
    except httpx.RequestError as exc:
        raise click.ClickException(
            f"Failed to cleanup remote workspace: {exc}"
        ) from exc
    return dict(_expect_mapping(data, "Remote workspace cleanup response"))


def download_workspace_manifest(
    *,
    base_url: str,
    session_id: str,
    headers: dict[str, str],
) -> dict[str, object]:
    try:
        with httpx.Client(base_url=base_url, headers=headers, timeout=60.0) as client:
            response = client.get(f"/sessions/{session_id}/workspace/archive/manifest")
            _raise_remote_http_error(response, "download remote workspace manifest")
            data = cast(object, response.json())
    except httpx.RequestError as exc:
        raise click.ClickException(
            f"Failed to download remote workspace manifest: {exc}"
        ) from exc
    return dict(_expect_mapping(data, "Remote workspace manifest response"))


def download_workspace_archive(
    *,
    base_url: str,
    session_id: str,
    headers: dict[str, str],
) -> str:
    try:
        with httpx.Client(base_url=base_url, headers=headers, timeout=60.0) as client:
            response = client.get(f"/sessions/{session_id}/workspace/archive")
            _raise_remote_http_error(response, "download remote workspace")
            data = cast(object, response.json())
    except httpx.RequestError as exc:
        raise click.ClickException(
            f"Failed to download remote workspace: {exc}"
        ) from exc
    if not isinstance(data, Mapping):
        raise click.ClickException("Remote workspace response must be a JSON object")
    payload = cast(Mapping[str, object], data)
    archive_base64 = payload.get("archive_base64")
    if not isinstance(archive_base64, str) or not archive_base64:
        raise click.ClickException("Remote workspace response missing archive_base64")
    return archive_base64


def download_workspace_diff(
    endpoint: RemoteEndpoint, session_id: str
) -> dict[str, object]:
    data = _get_remote_json(
        endpoint,
        f"/sessions/{session_id}/workspace/diff",
        "download remote workspace diff",
    )
    files = data.get("files")
    additions = data.get("additions")
    deletions = data.get("deletions")
    if not isinstance(files, list):
        raise click.ClickException("Remote workspace diff response missing files")
    if not isinstance(additions, int) or not isinstance(deletions, int):
        raise click.ClickException("Remote workspace diff response missing totals")
    return dict(data)


def download_workspace_patch(endpoint: RemoteEndpoint, session_id: str) -> str:
    data = _get_remote_json(
        endpoint,
        f"/sessions/{session_id}/workspace/patch",
        "download remote workspace patch",
    )
    patch_format = data.get("format")
    patch = data.get("patch")
    if patch_format != "unified_diff":
        raise click.ClickException("Remote workspace patch response has invalid format")
    if not isinstance(patch, str):
        raise click.ClickException("Remote workspace patch response missing patch")
    return patch


def publish_remote_branch(
    endpoint: RemoteEndpoint,
    session_id: str,
    branch_name: str,
) -> dict[str, object]:
    return publish_remote_result(
        endpoint, session_id, mode="branch", branch_name=branch_name
    )


def publish_remote_result(
    endpoint: RemoteEndpoint,
    session_id: str,
    *,
    mode: str,
    branch_name: str,
) -> dict[str, object]:
    data = _post_remote_json(
        endpoint,
        f"/sessions/{session_id}/publish",
        "publish remote workspace result",
        json={"mode": mode, "branch_name": branch_name},
    )
    if data.get("status") not in {"published", "partial", "unsupported", "failed"}:
        raise click.ClickException(
            "Remote result publication response has invalid status"
        )
    return dict(data)


def _get_remote_json(
    endpoint: RemoteEndpoint,
    path: str,
    action: str,
) -> dict[str, object]:
    try:
        with httpx.Client(
            base_url=endpoint.url,
            headers=auth_headers(endpoint),
            timeout=30.0,
        ) as client:
            response = client.get(path)
            _raise_remote_http_error(response, action)
            data = cast(object, response.json())
    except httpx.RequestError as exc:
        raise click.ClickException(f"Failed to {action}: {exc}") from exc
    return dict(_expect_mapping(data, f"Remote {action} response"))


def _post_remote_json(
    endpoint: RemoteEndpoint,
    path: str,
    action: str,
    *,
    json: dict[str, object] | None = None,
) -> dict[str, object]:
    try:
        with httpx.Client(
            base_url=endpoint.url,
            headers=auth_headers(endpoint),
            timeout=30.0,
        ) as client:
            response = (
                client.post(path, json=json) if json is not None else client.post(path)
            )
            _raise_remote_http_error(response, action)
            data = cast(object, response.json())
    except httpx.RequestError as exc:
        raise click.ClickException(f"Failed to {action}: {exc}") from exc
    return dict(_expect_mapping(data, f"Remote {action} response"))


def _expect_mapping(value: object, description: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise click.ClickException(f"{description} must be a JSON object")
    return cast(Mapping[str, object], value)


def delete_remote_session(
    *,
    base_url: str,
    session_id: str,
    headers: dict[str, str],
) -> None:
    try:
        with httpx.Client(base_url=base_url, headers=headers, timeout=30.0) as client:
            response = client.delete(f"/sessions/{session_id}")
            _raise_remote_http_error(response, "delete remote session")
    except httpx.RequestError as exc:
        raise click.ClickException(f"Failed to delete remote session: {exc}") from exc


def stream_prompt(
    *, base_url: str, session_id: str, prompt: str, headers: dict[str, str]
) -> int:
    timeout = httpx.Timeout(connect=10.0, write=30.0, pool=30.0, read=None)
    line_open = False
    try:
        with httpx.Client(
            base_url=base_url, headers=headers, timeout=timeout
        ) as client:
            with connect_sse(
                client,
                "POST",
                f"/sessions/{session_id}/prompt?event_format=display",
                json={"prompt": prompt},
            ) as event_source:
                _raise_remote_http_error(event_source.response, "stream remote prompt")
                for sse in event_source.iter_sse():
                    status, line_open = _handle_prompt_like_sse_event(
                        base_url=base_url,
                        session_id=session_id,
                        headers=headers,
                        event=sse.event,
                        data=sse.data,
                        line_open=line_open,
                    )
                    if status is not None:
                        if line_open:
                            click.echo()
                        return status
    except httpx.RequestError as exc:
        if line_open:
            click.echo()
        raise click.ClickException(f"Failed to stream remote prompt: {exc}") from exc
    if line_open:
        click.echo()
    raise click.ClickException("Remote prompt stream ended without TurnEnd")


def stream_prompt_or_run_request(
    *,
    base_url: str,
    session_id: str,
    prompt: str,
    headers: dict[str, str],
    display_consumer: DisplayWireConsumer | None = None,
) -> int:
    return _stream_prompt_like_request(
        base_url=base_url,
        session_id=session_id,
        path=f"/sessions/{session_id}/prompt?event_format=display",
        payload={"prompt": prompt},
        headers=headers,
        action="stream remote prompt",
        truncated_message="Remote prompt stream ended without RunRequested or TurnEnd",
        display_consumer=display_consumer,
    )


def stream_resume_or_run_request(
    *,
    base_url: str,
    session_id: str,
    prompt: str | None,
    headers: dict[str, str],
) -> int:
    payload: dict[str, str] = {"resume_reason": "remote_cli_resume"}
    if prompt is not None:
        payload["prompt"] = prompt
    return _stream_prompt_like_request(
        base_url=base_url,
        session_id=session_id,
        path=f"/sessions/{session_id}/resume?event_format=display",
        payload=payload,
        headers=headers,
        action="stream remote resume",
        truncated_message="Remote resume stream ended without RunRequested or TurnEnd",
    )


def _stream_prompt_like_request(
    *,
    base_url: str,
    session_id: str,
    path: str,
    payload: dict[str, str],
    headers: dict[str, str],
    action: str,
    truncated_message: str,
    display_consumer: DisplayWireConsumer | None = None,
) -> int:
    timeout = httpx.Timeout(connect=10.0, write=30.0, pool=30.0, read=None)
    line_open = False
    try:
        with httpx.Client(
            base_url=base_url, headers=headers, timeout=timeout
        ) as client:
            with connect_sse(
                client,
                "POST",
                path,
                json=payload,
            ) as event_source:
                _raise_remote_http_error(event_source.response, action)
                for sse in event_source.iter_sse():
                    if sse.event == "RunRequested":
                        if line_open:
                            click.echo()
                        payload = _parse_sse_payload(sse.data)
                        run_id = payload.get("run_id")
                        if not isinstance(run_id, str) or not run_id:
                            raise click.ClickException(
                                "Remote RunRequested event missing run_id"
                            )
                        click.echo(f"Requested local attached executor run {run_id}")
                        return 0
                    status, line_open = _handle_prompt_like_sse_event(
                        base_url=base_url,
                        session_id=session_id,
                        headers=headers,
                        event=sse.event,
                        data=sse.data,
                        line_open=line_open,
                        display_consumer=display_consumer,
                    )
                    if status is not None:
                        if line_open:
                            click.echo()
                        return status
    except httpx.RequestError as exc:
        if line_open:
            click.echo()
        raise click.ClickException(f"Failed to {action}: {exc}") from exc
    if line_open:
        click.echo()
    raise click.ClickException(truncated_message)


def _handle_prompt_like_sse_event(
    *,
    base_url: str,
    session_id: str,
    headers: dict[str, str],
    event: str,
    data: str,
    line_open: bool = False,
    display_consumer: DisplayWireConsumer | None = None,
) -> tuple[int | None, bool]:
    if display_consumer is not None:
        return _handle_prompt_like_sse_event_with_consumer(
            base_url=base_url,
            session_id=session_id,
            headers=headers,
            event=event,
            data=data,
            line_open=line_open,
            display_consumer=display_consumer,
        )
    if event in {
        "StreamDelta",
        "ThinkingDelta",
        "ToolCallDelta",
        "ToolResultDelta",
        "ApprovalRequest",
        "ApprovalResponse",
        "TurnStatusDelta",
        "TurnEnd",
        "Error",
    }:
        return handle_sse_event(
            base_url=base_url,
            session_id=session_id,
            headers=headers,
            event=event,
            data=data,
            line_open=line_open,
        )
    return handle_display_sse_event(
        base_url=base_url,
        session_id=session_id,
        headers=headers,
        event=event,
        data=data,
        line_open=line_open,
    )


def _handle_prompt_like_sse_event_with_consumer(
    *,
    base_url: str,
    session_id: str,
    headers: dict[str, str],
    event: str,
    data: str,
    line_open: bool,
    display_consumer: DisplayWireConsumer,
) -> tuple[int | None, bool]:
    del line_open
    legacy_event, payload = _legacy_prompt_event_payload(event, data)
    if legacy_event == "ApprovalRequest":
        request = _wire_message_from_payload(legacy_event, payload, session_id)
        if not isinstance(request, ApprovalRequest):
            raise click.ClickException("Remote approval event was not an approval request")
        response = asyncio.run(display_consumer.request_approval(request))
        _submit_approval(
            base_url=base_url,
            session_id=session_id,
            request_id=response.request_id,
            approved=response.approved,
            feedback=response.feedback,
            scope=response.scope,
            headers=headers,
        )
        return None, False
    if legacy_event == "Error":
        error = payload.get("error")
        raise click.ClickException(str(error) if error is not None else "Remote error")
    message = _wire_message_from_payload(legacy_event, payload, session_id)
    if message is None:
        return None, False
    asyncio.run(display_consumer.emit(message))
    if isinstance(message, TurnEnd):
        return (0 if message.completion_status is CompletionStatus.COMPLETED else 1), False
    return None, False


def _legacy_prompt_event_payload(
    event: str,
    data: str,
) -> tuple[str, dict[str, object]]:
    if event in {
        "StreamDelta",
        "ThinkingDelta",
        "ToolCallDelta",
        "ToolResultDelta",
        "ApprovalRequest",
        "ApprovalResponse",
        "TurnStatusDelta",
        "TurnEnd",
        "Error",
    }:
        return event, _parse_sse_payload(data)
    envelope = _parse_sse_payload(data)
    payload = envelope.get("payload")
    if not isinstance(payload, Mapping):
        raise click.ClickException("Remote display event payload missing payload")
    return (
        _legacy_event_name_for_display_kind(event),
        dict(cast(Mapping[str, object], payload)),
    )


def _wire_message_from_payload(
    event: str,
    payload: dict[str, object],
    session_id: str,
) -> WireMessage | None:
    message_session_id = _string_payload_value(payload, "session_id") or session_id
    agent_id = _string_payload_value(payload, "agent_id") or ""
    if event == "StreamDelta":
        return StreamDelta(
            session_id=message_session_id,
            agent_id=agent_id,
            content=_string_payload_value(payload, "content") or "",
            role=_string_payload_value(payload, "role") or "assistant",
        )
    if event == "ThinkingDelta":
        return ThinkingDelta(
            session_id=message_session_id,
            agent_id=agent_id,
            text=_string_payload_value(payload, "text") or "",
        )
    if event == "ToolCallDelta":
        return ToolCallDelta(
            session_id=message_session_id,
            agent_id=agent_id,
            tool_name=_string_payload_value(payload, "tool_name") or "unknown",
            arguments=_mapping_payload_value(payload, "arguments"),
            call_id=_string_payload_value(payload, "call_id") or "",
        )
    if event == "ToolResultDelta":
        result = payload.get("result")
        display_result = _string_payload_value(payload, "display_result") or ""
        if result is None:
            result = display_result
        return ToolResultDelta(
            session_id=message_session_id,
            agent_id=agent_id,
            call_id=_string_payload_value(payload, "call_id") or "",
            tool_name=_string_payload_value(payload, "tool_name") or "unknown",
            result=result,
            display_result=display_result,
            is_error=bool(payload.get("is_error")),
        )
    if event == "ApprovalRequest":
        tool_call = _tool_call_payload_value(payload, message_session_id, agent_id)
        return ApprovalRequest(
            session_id=message_session_id,
            agent_id=agent_id,
            request_id=_string_payload_value(payload, "request_id") or "",
            call_id=_string_payload_value(payload, "call_id") or "",
            tool_call=tool_call,
            tool=_string_payload_value(payload, "tool") or "",
            args=_mapping_payload_value(payload, "args"),
            risk_level=cast(Any, _string_payload_value(payload, "risk_level") or "low"),
        )
    if event == "ApprovalResponse":
        return ApprovalResponse(
            session_id=message_session_id,
            agent_id=agent_id,
            request_id=_string_payload_value(payload, "request_id") or "",
            call_id=_string_payload_value(payload, "call_id") or "",
            approved=bool(payload.get("approved", True)),
            feedback=cast(str | None, payload.get("feedback")),
            scope=cast(Any, _string_payload_value(payload, "scope") or "once"),
        )
    if event == "TurnStatusDelta":
        return TurnStatusDelta(
            session_id=message_session_id,
            agent_id=agent_id,
            phase=_string_payload_value(payload, "phase") or "idle",
            elapsed_seconds=_float_payload_value(payload, "elapsed_seconds"),
            tokens_in=_int_payload_value(payload, "tokens_in"),
            tokens_out=_int_payload_value(payload, "tokens_out"),
            model_name=_string_payload_value(payload, "model_name") or "",
            context_percent=_float_payload_value(payload, "context_percent"),
        )
    if event == "TurnEnd":
        status = _string_payload_value(payload, "completion_status") or "error"
        return TurnEnd(
            session_id=message_session_id,
            agent_id=agent_id,
            turn_id=_string_payload_value(payload, "turn_id") or "",
            completion_status=CompletionStatus(status),
        )
    return None


def _tool_call_payload_value(
    payload: dict[str, object],
    session_id: str,
    agent_id: str,
) -> ToolCallDelta | None:
    tool_call = payload.get("tool_call")
    if not isinstance(tool_call, Mapping):
        return None
    tool_call_payload = dict(cast(Mapping[str, object], tool_call))
    return ToolCallDelta(
        session_id=_string_payload_value(tool_call_payload, "session_id") or session_id,
        agent_id=_string_payload_value(tool_call_payload, "agent_id") or agent_id,
        tool_name=_string_payload_value(tool_call_payload, "tool_name") or "unknown",
        arguments=_mapping_payload_value(tool_call_payload, "arguments"),
        call_id=_string_payload_value(tool_call_payload, "call_id") or "",
    )


def _string_payload_value(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _mapping_payload_value(payload: Mapping[str, object], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(Mapping[str, Any], value))


def _int_payload_value(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool):
        return 0
    return value if isinstance(value, int) else 0


def _float_payload_value(payload: Mapping[str, object], key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def attach_remote_session(
    *, base_url: str, session_id: str, headers: dict[str, str]
) -> int:
    timeout = httpx.Timeout(connect=10.0, write=30.0, pool=30.0, read=None)
    line_open = False
    try:
        with httpx.Client(
            base_url=base_url, headers=headers, timeout=timeout
        ) as client:
            with connect_sse(
                client,
                "GET",
                f"/sessions/{session_id}/display-events",
            ) as event_source:
                _raise_remote_http_error(event_source.response, "attach remote session")
                for sse in event_source.iter_sse():
                    if sse.event == "ping":
                        continue
                    status, line_open = handle_display_sse_event(
                        base_url=base_url,
                        session_id=session_id,
                        headers=headers,
                        event=sse.event,
                        data=sse.data,
                        line_open=line_open,
                    )
                    if status is not None:
                        if line_open:
                            click.echo()
                        return status
    except httpx.RequestError as exc:
        if line_open:
            click.echo()
        raise click.ClickException(f"Failed to attach remote session: {exc}") from exc
    if line_open:
        click.echo()
    return 0


def handle_display_sse_event(
    *,
    base_url: str,
    session_id: str,
    headers: dict[str, str],
    event: str,
    data: str,
    line_open: bool = False,
) -> tuple[int | None, bool]:
    envelope = _parse_sse_payload(data)
    payload = envelope.get("payload")
    if not isinstance(payload, Mapping):
        raise click.ClickException("Remote display event payload missing payload")
    return handle_sse_event(
        base_url=base_url,
        session_id=session_id,
        headers=headers,
        event=_legacy_event_name_for_display_kind(event),
        data=json.dumps(dict(cast(Mapping[str, object], payload))),
        line_open=line_open,
    )


def _legacy_event_name_for_display_kind(display_kind: str) -> str:
    match display_kind:
        case "assistant_text_delta":
            return "StreamDelta"
        case "thinking_delta":
            return "ThinkingDelta"
        case "tool_call":
            return "ToolCallDelta"
        case "tool_result":
            return "ToolResultDelta"
        case "approval_prompt":
            return "ApprovalRequest"
        case "approval_result":
            return "ApprovalResponse"
        case "progress_update":
            return "TurnStatusDelta"
        case "final_result":
            return "TurnEnd"
        case _:
            return display_kind


def handle_sse_event(
    *,
    base_url: str,
    session_id: str,
    headers: dict[str, str],
    event: str,
    data: str,
    line_open: bool = False,
) -> tuple[int | None, bool]:
    try:
        payload = _parse_sse_payload(data)
    except click.ClickException:
        if line_open:
            click.echo()
        raise
    if event == "StreamDelta":
        content = payload.get("content")
        if isinstance(content, str) and content:
            click.echo(content, nl=False)
            line_open = True
        return None, line_open
    if event == "ThinkingDelta":
        text = payload.get("text")
        if isinstance(text, str) and text:
            click.echo(text, nl=False)
            line_open = True
        return None, line_open
    if event == "ToolCallDelta":
        line_open = _end_inline_stream_line(line_open)
        tool_name = payload.get("tool_name")
        if isinstance(tool_name, str):
            click.echo(f"[tool] {tool_name}")
        return None, line_open
    if event == "ToolResultDelta":
        line_open = _end_inline_stream_line(line_open)
        display_result = payload.get("display_result")
        if isinstance(display_result, str) and display_result:
            click.echo(display_result)
        return None, line_open
    if event == "ApprovalRequest":
        line_open = _end_inline_stream_line(line_open)
        request_id = payload.get("request_id")
        if isinstance(request_id, str):
            decision = _prompt_for_approval(payload)
            _submit_approval(
                base_url=base_url,
                session_id=session_id,
                request_id=request_id,
                approved=decision.approved,
                feedback=decision.feedback,
                scope=decision.scope,
                headers=headers,
            )
        return None, line_open
    if event == "Error":
        line_open = _end_inline_stream_line(line_open)
        error = payload.get("error")
        raise click.ClickException(str(error) if error is not None else "Remote error")
    if event == "TurnEnd":
        status = payload.get("completion_status")
        return (0 if status == "completed" else 1), line_open
    return None, line_open


@dataclass(frozen=True)
class _ApprovalDecision:
    approved: bool
    feedback: str | None = None
    scope: str = "once"


def _prompt_for_approval(payload: dict[str, object]) -> _ApprovalDecision:
    tool_call = payload.get("tool_call")
    tool_name = "unknown"
    if isinstance(tool_call, Mapping):
        tool_call = cast(Mapping[str, object], tool_call)
        raw_tool_name = tool_call.get("tool_name")
        if isinstance(raw_tool_name, str) and raw_tool_name:
            tool_name = raw_tool_name
        click.echo(f"[approval] Remote tool request {tool_name}")
        arguments = tool_call.get("arguments")
        if isinstance(arguments, Mapping):
            click.echo(
                json.dumps(
                    dict(cast(Mapping[str, object], arguments)),
                    indent=2,
                    sort_keys=True,
                )
            )
    click.echo(
        "[y]=approve  [a]=approve all (session)  [n]=reject  [r]=reject with reason"
    )
    choice = _approval_prompt("→", default="n").strip().lower()
    if choice in ("y", "yes"):
        return _ApprovalDecision(approved=True, scope="once")
    if choice in ("a", "all"):
        return _ApprovalDecision(approved=True, scope="session")
    if choice in ("r", "reason"):
        feedback = _approval_prompt("Reason", default="Rejected by user")
        return _ApprovalDecision(
            approved=False,
            feedback=feedback.strip() or "Rejected by user",
            scope="once",
        )
    return _ApprovalDecision(approved=False, feedback="Rejected by user", scope="once")


def _approval_prompt(text: str, *, default: str) -> str:
    try:
        return cast(str, click.prompt(text, default=default, show_default=False))
    except click.Abort as exc:
        raise click.ClickException(
            "Remote approval requires input; rerun with --approval yolo to let "
            + "the server approve tools, or provide approval input on stdin."
        ) from exc


def _end_inline_stream_line(line_open: bool) -> bool:
    if line_open:
        click.echo()
    return False


def _submit_approval(
    *,
    base_url: str,
    session_id: str,
    request_id: str,
    approved: bool,
    feedback: str | None,
    scope: str,
    headers: dict[str, str],
) -> None:
    payload: dict[str, object] = {
        "request_id": request_id,
        "approved": approved,
        "scope": scope,
    }
    if feedback is not None:
        payload["feedback"] = feedback
    try:
        with httpx.Client(base_url=base_url, headers=headers, timeout=30.0) as client:
            response = client.post(
                f"/sessions/{session_id}/approve",
                json=payload,
            )
            _raise_remote_http_error(response, "approve remote tool request")
    except httpx.RequestError as exc:
        raise click.ClickException(
            f"Failed to approve remote tool request: {exc}"
        ) from exc


def _parse_sse_payload(data: str) -> dict[str, object]:
    if not data:
        return {}
    try:
        payload = cast(object, json.loads(data))
    except json.JSONDecodeError as exc:
        raise click.ClickException(
            f"Remote SSE event payload must be valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise click.ClickException("Remote SSE event payload must be a JSON object")
    return dict(cast(Mapping[str, object], payload))


def _raise_remote_http_error(response: httpx.Response, action: str) -> None:
    try:
        _ = response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = _response_error_detail(response)
        raise click.ClickException(f"Failed to {action}: {detail}") from exc


def _response_error_detail(response: httpx.Response) -> str:
    if not hasattr(response, "json"):
        status_code = getattr(response, "status_code", None)
        return f"HTTP {status_code}" if isinstance(status_code, int) else "HTTP error"
    try:
        payload = cast(object, response.json())
    except ValueError:
        text = response.text.strip()
        if text:
            return text
        return f"HTTP {response.status_code}"
    if isinstance(payload, Mapping):
        payload = cast(Mapping[str, object], payload)
        detail = payload.get("detail")
        if isinstance(detail, str) and detail:
            return detail
    return f"HTTP {response.status_code}"


def _remote_payload(endpoint: RemoteEndpoint) -> dict[str, str]:
    payload = {"url": endpoint.url}
    if endpoint.token is not None:
        payload["token"] = endpoint.token
    return payload
