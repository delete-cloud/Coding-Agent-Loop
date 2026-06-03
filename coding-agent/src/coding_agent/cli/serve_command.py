from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import click

from coding_agent.remote.approval import APPROVAL_POLICIES


DAEMON_APPROVAL_CHOICES = click.Choice(APPROVAL_POLICIES)


def _load_server_cli_settings(config_path: Path | None) -> dict[str, Any]:
    if config_path is None:
        return {}
    from agentkit.config.loader import load_config

    server_config = load_config(config_path).extra.get("server", {})
    if not isinstance(server_config, dict):
        raise click.ClickException("[server] config must be a table")
    return cast(dict[str, Any], server_config)


def _server_cli_host(server_config: dict[str, Any], host: str | None) -> str:
    if host is not None:
        return host
    configured_host = server_config.get("host")
    if configured_host is None:
        return "127.0.0.1"
    if not isinstance(configured_host, str) or not configured_host.strip():
        raise click.ClickException("server.host must be a non-empty string")
    return configured_host.strip()


def _server_cli_port(server_config: dict[str, Any], port: int | None) -> int:
    if port is not None:
        return port
    configured_port = server_config.get("port")
    if configured_port is None:
        return 8080
    if (
        isinstance(configured_port, bool)
        or not isinstance(configured_port, int)
        or configured_port <= 0
    ):
        raise click.ClickException("server.port must be a positive integer")
    return configured_port


def _run_http_control_plane(
    *,
    config_path: Path | None,
    host: str | None,
    port: int | None,
    label: str,
) -> None:
    import uvicorn

    previous_server_config = os.environ.get("CODING_AGENT_SERVER_CONFIG")
    if config_path is not None:
        os.environ["CODING_AGENT_SERVER_CONFIG"] = str(config_path.resolve())
    try:
        server_config = _load_server_cli_settings(config_path)
        resolved_host = _server_cli_host(server_config, host)
        resolved_port = _server_cli_port(server_config, port)

        from coding_agent.server.http_server import app

        click.echo(f"Starting Coding Agent {label} on {resolved_host}:{resolved_port}")
        uvicorn.run(app, host=resolved_host, port=resolved_port)
    finally:
        if config_path is not None:
            if previous_server_config is None:
                os.environ.pop("CODING_AGENT_SERVER_CONFIG", None)
            else:
                os.environ["CODING_AGENT_SERVER_CONFIG"] = previous_server_config


@click.command()
@click.option("--port", default=None, type=int, help="Server port")
@click.option("--host", default=None, help="Server host")
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Explicit server config file.",
)
def serve(port: int | None, host: str | None, config_path: Path | None):
    """Start HTTP API server."""
    _run_http_control_plane(
        config_path=config_path,
        host=host,
        port=port,
        label="HTTP server",
    )


@click.group(invoke_without_command=True)
@click.option("--port", default=None, type=int, help="Daemon port")
@click.option("--host", default=None, help="Daemon host")
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Explicit daemon config file.",
)
@click.pass_context
def daemon(
    ctx: click.Context,
    port: int | None,
    host: str | None,
    config_path: Path | None,
) -> None:
    """Start local daemon control plane or use daemon-backed clients."""
    if ctx.invoked_subcommand is not None:
        return
    _run_http_control_plane(
        config_path=config_path,
        host=host,
        port=port,
        label="local daemon control plane",
    )


@daemon.command("run")
@click.option("--goal", required=True, help="Prompt to send to the local daemon.")
@click.option("--repo", default=".", help="Local repository path for the session.")
@click.option(
    "--url",
    default="http://127.0.0.1:8080",
    show_default=True,
    help="Local daemon HTTP URL.",
)
@click.option(
    "--approval",
    "approval_policy",
    default="auto",
    show_default=True,
    type=DAEMON_APPROVAL_CHOICES,
    help="Tool approval policy for the daemon session.",
)
@click.option("--token", default=None, help="Bearer token for the daemon.")
@click.option(
    "--keep-session/--cleanup-session",
    default=True,
    show_default=True,
    help="Keep the created daemon session after the prompt finishes.",
)
def daemon_run(
    goal: str,
    repo: str,
    url: str,
    approval_policy: str,
    token: str | None,
    keep_session: bool,
) -> None:
    """Run one prompt through an already-running local daemon."""
    from coding_agent.remote.client import (
        RemoteEndpoint,
        auth_headers,
        create_local_daemon_session,
        delete_remote_session,
        stream_prompt_or_run_request,
    )

    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.is_dir():
        raise click.ClickException(f"--repo must be an existing directory: {repo}")
    endpoint = RemoteEndpoint(
        name="local-daemon",
        url=url.rstrip("/"),
        token=token,
    )
    if not endpoint.url:
        raise click.ClickException("--url must not be empty")
    session_id = create_local_daemon_session(
        endpoint,
        repo_path=repo_path,
        approval_policy=approval_policy,
    )
    click.echo(f"Created daemon-backed local session {session_id} at {endpoint.url}")
    headers = auth_headers(endpoint)
    try:
        status = stream_prompt_or_run_request(
            base_url=endpoint.url,
            session_id=session_id,
            prompt=goal,
            headers=headers,
        )
    finally:
        if not keep_session:
            delete_remote_session(
                base_url=endpoint.url,
                session_id=session_id,
                headers=headers,
            )
            click.echo(f"Cleaned up daemon-backed local session {session_id}")
    raise SystemExit(status)
