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
            name: _remote_payload(endpoint) for name, endpoint in sorted(remotes.items())
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


def create_remote_session(endpoint: RemoteEndpoint) -> str:
    payload: dict[str, object] = {
        "workspace_source": {"kind": "docker"},
        "approval_policy": "auto",
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
        raise click.ClickException(
            f"Failed to create remote session: {exc}"
        ) from exc
    if not isinstance(data, Mapping):
        raise click.ClickException("Remote session response must be a JSON object")
    data = cast(Mapping[str, object], data)
    session_id = data.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise click.ClickException("Remote session response missing session_id")
    return session_id


def stream_prompt(
    *, base_url: str, session_id: str, prompt: str, headers: dict[str, str]
) -> int:
    timeout = httpx.Timeout(connect=10.0, write=30.0, pool=30.0, read=None)
    try:
        with httpx.Client(base_url=base_url, headers=headers, timeout=timeout) as client:
            with connect_sse(
                client,
                "POST",
                f"/sessions/{session_id}/prompt",
                json={"prompt": prompt},
            ) as event_source:
                _raise_remote_http_error(
                    event_source.response, "stream remote prompt"
                )
                for sse in event_source.iter_sse():
                    status = handle_sse_event_for_test(
                        base_url=base_url,
                        session_id=session_id,
                        headers=headers,
                        event=sse.event,
                        data=sse.data,
                    )
                    if status is not None:
                        return status
    except httpx.RequestError as exc:
        raise click.ClickException(f"Failed to stream remote prompt: {exc}") from exc
    raise click.ClickException("Remote prompt stream ended without TurnEnd")


def handle_sse_event_for_test(
    *, base_url: str, session_id: str, headers: dict[str, str], event: str, data: str
) -> int | None:
    payload = _parse_sse_payload(data)
    if event == "StreamDelta":
        content = payload.get("content")
        if isinstance(content, str):
            click.echo(content, nl=False)
        return None
    if event == "ThinkingDelta":
        text = payload.get("text")
        if isinstance(text, str):
            click.echo(text, nl=False)
        return None
    if event == "ToolCallDelta":
        tool_name = payload.get("tool_name")
        if isinstance(tool_name, str):
            click.echo(f"\n[tool] {tool_name}")
        return None
    if event == "ToolResultDelta":
        display_result = payload.get("display_result")
        if isinstance(display_result, str) and display_result:
            click.echo(display_result)
        return None
    if event == "ApprovalRequest":
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
        return None
    if event == "Error":
        error = payload.get("error")
        raise click.ClickException(str(error) if error is not None else "Remote error")
    if event == "TurnEnd":
        status = payload.get("completion_status")
        return 0 if status == "completed" else 1
    return None


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
        arguments = tool_call.get("arguments")
        if isinstance(arguments, Mapping):
            click.echo(json.dumps(dict(arguments), indent=2, sort_keys=True))
    approved = click.confirm(
        f"Approve remote tool request {tool_name}?",
        default=False,
    )
    return _ApprovalDecision(approved=approved)


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
