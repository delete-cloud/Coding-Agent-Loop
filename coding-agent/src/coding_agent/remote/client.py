"""Client helpers for remote Coding Agent HTTP sessions."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import click
import httpx
from httpx_sse import connect_sse


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
    approval_policy: str = "auto",
) -> str:
    if approval_policy not in {"auto", "interactive", "yolo"}:
        raise click.ClickException(f"Unsupported approval policy: {approval_policy}")
    workspace_source: dict[str, object] = {"kind": "docker"}
    if snapshot_archive_base64 is not None:
        workspace_source["snapshot_archive_base64"] = snapshot_archive_base64
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
    return dict(data)


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
) -> dict[str, object]:
    try:
        with httpx.Client(
            base_url=endpoint.url,
            headers=auth_headers(endpoint),
            timeout=30.0,
        ) as client:
            response = client.post(path)
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
                f"/sessions/{session_id}/prompt",
                json={"prompt": prompt},
            ) as event_source:
                _raise_remote_http_error(event_source.response, "stream remote prompt")
                for sse in event_source.iter_sse():
                    status, line_open = handle_sse_event(
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
