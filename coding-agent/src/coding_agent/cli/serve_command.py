from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import click


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
    import uvicorn

    previous_server_config = os.environ.get("CODING_AGENT_SERVER_CONFIG")
    if config_path is not None:
        os.environ["CODING_AGENT_SERVER_CONFIG"] = str(config_path.resolve())
    try:
        server_config = _load_server_cli_settings(config_path)
        resolved_host = _server_cli_host(server_config, host)
        resolved_port = _server_cli_port(server_config, port)

        from coding_agent.ui.http_server import app

        click.echo(
            f"Starting Coding Agent HTTP server on {resolved_host}:{resolved_port}"
        )
        uvicorn.run(app, host=resolved_host, port=resolved_port)
    finally:
        if config_path is not None:
            if previous_server_config is None:
                os.environ.pop("CODING_AGENT_SERVER_CONFIG", None)
            else:
                os.environ["CODING_AGENT_SERVER_CONFIG"] = previous_server_config
