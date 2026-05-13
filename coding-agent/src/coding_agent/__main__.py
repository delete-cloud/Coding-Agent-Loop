"""CLI entry point: python -m coding_agent"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path
from collections.abc import Mapping
from typing import Any, cast, get_args

import click

from coding_agent.adapter import PipelineAdapter
from coding_agent.core.config import Config, load_config
from coding_agent.postmortem_phase1 import build_phase1_artifacts
from coding_agent.remote.approval import APPROVAL_POLICIES
from coding_agent.ui.headless import HeadlessConsumer
from coding_agent.ui.rich_tui import CodingAgentTUI
from coding_agent.verification import VerificationRunner, load_task_packet_contract

# ------------------------------------------------------------------
# Construction logic lives in app.py — re-export for backward compat.
# Callers, tests, and session_manager import these names from __main__.
# ------------------------------------------------------------------
from coding_agent.app import create_agent, create_child_pipeline  # noqa: F401


CLI_PROVIDER_CHOICES = click.Choice(
    [str(provider) for provider in get_args(Config.model_fields["provider"].annotation)]
)
REMOTE_APPROVAL_CHOICES = click.Choice(APPROVAL_POLICIES)


def _collect_shared_cli_args(
    *,
    model: str | None,
    provider_name: str | None,
    base_url: str | None,
    api_key: str | None,
) -> dict[str, object]:
    cli_args: dict[str, object] = {}
    if provider_name is not None:
        cli_args["provider"] = provider_name
    if model is not None:
        cli_args["model"] = model
    if base_url is not None:
        cli_args["base_url"] = base_url
    if api_key is not None:
        cli_args["api_key"] = api_key
    return cli_args


def _get_shared_cli_args(ctx: click.Context) -> dict[str, object]:
    if isinstance(ctx.obj, dict):
        shared_args = ctx.obj.get("shared_cli_args")
        if isinstance(shared_args, dict):
            return dict(shared_args)
    return {}


def _build_runtime_config(
    ctx: click.Context, command_args: dict[str, object] | None = None
) -> Config:
    cli_args = _get_shared_cli_args(ctx)
    if command_args:
        for key, value in command_args.items():
            if value is not None:
                cli_args[key] = value
    return load_config(cli_args=cli_args or None)


@click.group(invoke_without_command=True)
@click.option("--model", default=None, help="Model name")
@click.option(
    "--provider",
    "provider_name",
    default=None,
    type=CLI_PROVIDER_CHOICES,
)
@click.option("--base-url", default=None, help="OpenAI-compatible API base URL")
@click.option("--api-key", default=None, help="API key")
@click.pass_context
def main(ctx, model, provider_name, base_url, api_key):
    """Coding Agent CLI.

    Without subcommand: starts interactive REPL mode (default)
    """
    ctx.ensure_object(dict)
    ctx.obj["shared_cli_args"] = _collect_shared_cli_args(
        model=model,
        provider_name=provider_name,
        base_url=base_url,
        api_key=api_key,
    )

    if ctx.invoked_subcommand is None:
        # Default to interactive REPL mode
        import asyncio
        from coding_agent.cli.repl import run_repl

        if not sys.stdout.isatty():
            raise click.UsageError(
                "interactive REPL mode requires an interactive terminal; use 'python -m coding_agent run --goal \"<task>\"' for batch mode"
            )

        config = _build_runtime_config(ctx)
        asyncio.run(run_repl(config))


def _load_kb_cli_settings(
    config_path: Path, db_path: str | None
) -> tuple[Path, dict[str, Any]]:
    from agentkit.config.loader import load_config

    kb_cfg: dict[str, Any] = {}
    if config_path.exists():
        agent_cfg = load_config(config_path)
        raw_kb_cfg = agent_cfg.extra.get("kb", {})
        if isinstance(raw_kb_cfg, dict):
            kb_cfg = raw_kb_cfg

    resolved_db = (
        Path(db_path)
        if db_path is not None
        else Path(os.environ.get("AGENT_DATA_DIR", "./data"))
        / str(kb_cfg.get("db_path", "kb"))
    )
    return resolved_db, kb_cfg


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


@main.command()
@click.option("--goal", required=True, help="Task goal for the agent")
@click.option("--repo", default=".", help="Repository path")
@click.option("--max-steps", default=30, help="Max steps per turn")
@click.option("--approval", default="yolo", type=REMOTE_APPROVAL_CHOICES)
@click.option(
    "--parallel/--no-parallel", default=True, help="Enable parallel tool execution"
)
@click.option("--max-parallel", default=5, help="Maximum parallel tool executions")
@click.option("--cache/--no-cache", default=True, help="Enable tool result caching")
@click.option("--cache-size", default=100, help="Maximum cached entries")
@click.option("--tui", is_flag=True, help="Use Rich TUI interface (batch mode)")
@click.pass_context
def run(
    ctx,
    goal,
    repo,
    max_steps,
    approval,
    parallel,
    max_parallel,
    cache,
    cache_size,
    tui,
):
    """Run agent on a goal (batch mode)."""
    import asyncio

    config = _build_runtime_config(
        ctx,
        command_args={
            "repo": repo,
            "max_steps": max_steps,
            "approval_mode": approval,
            "enable_parallel_tools": parallel,
            "max_parallel_tools": max_parallel,
            "enable_cache": cache,
            "cache_size": cache_size,
        },
    )

    if tui:
        asyncio.run(_run_with_tui(config, goal))
    else:
        asyncio.run(_run_headless(config, goal))


@main.command()
@click.option("--repo", default=".", help="Repository path")
@click.option("--max-steps", default=30, help="Max steps per turn")
@click.pass_context
def repl(ctx, repo, max_steps):
    """Start interactive REPL mode (explicit)."""
    import asyncio
    from coding_agent.cli.repl import run_repl

    config = _build_runtime_config(
        ctx,
        command_args={
            "repo": repo,
            "max_steps": max_steps,
            "approval_mode": "yolo",
        },
    )
    asyncio.run(run_repl(config))


@main.group()
def kb():
    pass


@main.group()
def postmortem():
    """Postmortem knowledge-base tooling."""


@postmortem.command("phase1")
@click.option(
    "--repo",
    default=Path("."),
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Repository root used for git-history collection.",
)
@click.option(
    "--output-dir",
    default="postmortem",
    help="Output directory relative to --repo unless absolute.",
)
def postmortem_phase1(repo: Path, output_dir: str) -> None:
    """Generate the Phase 1 postmortem onboarding artifacts."""
    target_output = Path(output_dir)
    if not target_output.is_absolute():
        target_output = repo / target_output
    result = build_phase1_artifacts(repo, output_dir=target_output)
    click.echo(
        "Generated Phase 1 postmortem onboarding artifacts "
        f"at {result.output_dir} ({result.pattern_count} patterns from {result.commit_count} fix commits)."
    )


@kb.command("index")
@click.argument("path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--db-path",
    default=None,
    help="LanceDB database path (default: from agent.toml [kb].db_path)",
)
def kb_index(path: Path, db_path: str | None):
    import asyncio

    from coding_agent.kb import KB

    config_path = Path(__file__).parent / "agent.toml"
    resolved_db, kb_cfg = _load_kb_cli_settings(config_path, db_path)

    probe_kb = KB(
        db_path=resolved_db, embedding_dim=int(kb_cfg.get("embedding_dim", 1536))
    )
    if probe_kb.has_table():
        click.echo(
            "Chunks table already exists. Skipping. (Phase 1 does not support incremental updates.)"
        )
        return

    raw_extensions = kb_cfg.get(
        "index_extensions",
        [".md", ".txt", ".rst", ".yaml", ".yml", ".toml"],
    )
    if not isinstance(raw_extensions, list):
        raise TypeError("[kb].index_extensions must be a list")

    kb_instance = KB(
        db_path=resolved_db,
        embedding_model=str(kb_cfg.get("embedding_model", "text-embedding-3-small")),
        embedding_dim=int(kb_cfg.get("embedding_dim", 1536)),
        chunk_size=int(kb_cfg.get("chunk_size", 1200)),
        chunk_overlap=int(kb_cfg.get("chunk_overlap", 200)),
        text_extensions={str(ext) for ext in raw_extensions},
    )

    asyncio.run(kb_instance.index_directory(path, show_progress=False))
    click.echo("Done.")


@kb.command("search")
@click.argument("query")
@click.option("--k", default=5, type=int, help="Number of results to return")
@click.option(
    "--db-path",
    default=None,
    help="LanceDB database path (default: from agent.toml [kb].db_path)",
)
def kb_search(query: str, k: int, db_path: str | None):
    from coding_agent.kb import KB

    config_path = Path(__file__).parent / "agent.toml"
    resolved_db, kb_cfg = _load_kb_cli_settings(config_path, db_path)

    kb_instance = KB(
        db_path=resolved_db,
        embedding_model=str(kb_cfg.get("embedding_model", "text-embedding-3-small")),
        embedding_dim=int(kb_cfg.get("embedding_dim", 1536)),
        chunk_size=int(kb_cfg.get("chunk_size", 1200)),
        chunk_overlap=int(kb_cfg.get("chunk_overlap", 200)),
    )

    if not kb_instance.has_table():
        click.echo("No index found. Run 'kb index <path>' first.")
        return

    results = kb_instance.search_sync(query, k=k)
    if not results:
        click.echo("No results found.")
        return

    for index, result in enumerate(results, start=1):
        click.echo(f"\n--- Result {index} (score: {result.score:.4f}) ---")
        click.echo(f"Source: {result.chunk.source}")
        content = result.chunk.content
        if len(content) > 200:
            content = content[:200] + "..."
        click.echo(content)


async def _run_with_tui(config, goal):
    """Run agent with TUI display."""
    api_key = config.api_key.get_secret_value() if config.api_key else None
    pipeline, ctx = create_agent(
        api_key=api_key,
        model_override=config.model,
        provider_override=config.provider,
        base_url_override=config.base_url,
        workspace_root=config.repo,
        max_steps_override=config.max_steps,
        approval_mode_override=config.approval_mode,
    )
    tui = CodingAgentTUI(model_name=config.model, max_steps=config.max_steps)
    adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=tui.consumer)
    with tui:
        tui.add_user_message(goal)
        result = await adapter.run_turn(goal)
        click.echo(f"\n--- Result ({result.stop_reason}) ---")


async def _run_headless(config, goal):
    """Run agent in headless mode."""
    api_key = config.api_key.get_secret_value() if config.api_key else None
    pipeline, ctx = create_agent(
        api_key=api_key,
        model_override=config.model,
        provider_override=config.provider,
        base_url_override=config.base_url,
        workspace_root=config.repo,
        max_steps_override=config.max_steps,
        approval_mode_override=config.approval_mode,
    )
    consumer = HeadlessConsumer()
    adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)
    result = await adapter.run_turn(goal)
    click.echo(f"\n--- Result ({result.stop_reason}) ---")
    if result.final_message:
        click.echo(result.final_message)


def _create_provider(config):
    """Create the appropriate provider based on config."""
    if config.provider == "anthropic":
        from coding_agent.providers.anthropic import AnthropicProvider

        return AnthropicProvider(
            model=config.model,
            api_key=config.api_key,
        )
    else:
        from coding_agent.providers.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
        )


@main.command()
@click.option("--session", "-s", help="Session ID (default: last)")
def stats(session: str | None):
    """Show session statistics."""
    from coding_agent.metrics import collector

    if not session:
        # Use last session
        sessions = collector.list_sessions()
        if not sessions:
            click.echo("No sessions found.")
            return
        session = sessions[-1]

    metrics = collector.get_session(session)
    if not metrics:
        click.echo(f"Session {session} not found.")
        return

    data = metrics.to_dict()
    tool_calls = cast(Mapping[str, int], data["tool_calls"])

    click.echo(f"Session: {data['session_id']}")
    click.echo(f"Duration: {data['duration']}")
    click.echo(f"\nTools: {data['tools_total']} calls")
    for tool, count in tool_calls.items():
        click.echo(f"  • {tool}: {count}")
    click.echo(
        f"\nAPI: {data['api_calls']} calls, avg latency {data['avg_api_latency']}"
    )
    click.echo(f"Cache hit rate: {data['cache_hit_rate']}")
    click.echo(f"Tokens: {data['tokens_input']} in / {data['tokens_output']} out")


@main.command()
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


@main.group()
def remote() -> None:
    """Manage and use remote Coding Agent servers."""


@remote.command("add")
@click.argument("name")
@click.argument("url")
@click.option("--token", default=None, help="Bearer token for the remote server")
def remote_add(name: str, url: str, token: str | None) -> None:
    """Add or update a named remote endpoint."""
    from coding_agent.remote.client import add_remote

    endpoint = add_remote(name, url, token)
    click.echo(f"Added remote {endpoint.name}: {endpoint.url}")


@remote.command("list")
def remote_list() -> None:
    """List named remote endpoints."""
    from coding_agent.remote.client import load_remotes

    remotes = load_remotes()
    if not remotes:
        click.echo("No remotes configured.")
        return
    for endpoint in remotes.values():
        auth = "token" if endpoint.token is not None else "no-token"
        click.echo(f"{endpoint.name}\t{endpoint.url}\t{auth}")


@remote.command("remove")
@click.argument("name")
def remote_remove(name: str) -> None:
    """Remove a named remote endpoint."""
    from coding_agent.remote.client import remove_remote

    remove_remote(name)
    click.echo(f"Removed remote {name}")


@remote.command("repl")
@click.argument("name")
@click.option(
    "--repo",
    default=None,
    help="Upload a local workspace snapshot and download the final remote workspace into it.",
)
@click.option(
    "--empty-workspace",
    is_flag=True,
    help="Create an empty server-side Docker workspace.",
)
@click.option(
    "--runtime",
    "runtime_profile",
    default=None,
    help="Use a server allowlisted runtime profile for the remote workspace.",
)
@click.option(
    "--goal", required=True, help="Initial prompt to send to the remote session"
)
@click.option(
    "--approval",
    "approval_policy",
    default="auto",
    show_default=True,
    type=REMOTE_APPROVAL_CHOICES,
    help="Remote tool approval policy for the created session.",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Download and overwrite the local workspace without confirmation.",
)
def remote_repl(
    name: str,
    repo: str | None,
    empty_workspace: bool,
    runtime_profile: str | None,
    goal: str,
    approval_policy: str,
    yes: bool,
) -> None:
    """Compatibility alias for remote run; creates a one-shot remote run."""
    _remote_run_once(
        name=name,
        repo=repo,
        empty_workspace=empty_workspace,
        runtime_profile=runtime_profile,
        goal=goal,
        approval_policy=approval_policy,
        yes=yes,
        download_results=repo is not None,
        cleanup_on_success=True,
    )


@remote.command("run")
@click.argument("name")
@click.option(
    "--repo",
    default=None,
    help="Upload a local workspace snapshot. Results stay remote unless --download is passed.",
)
@click.option(
    "--empty-workspace",
    is_flag=True,
    help="Create an empty server-side Docker workspace.",
)
@click.option(
    "--runtime",
    "runtime_profile",
    default=None,
    help="Use a server allowlisted runtime profile for the remote workspace.",
)
@click.option(
    "--goal", required=True, help="Initial prompt to send to the remote session"
)
@click.option(
    "--approval",
    "approval_policy",
    default="auto",
    show_default=True,
    type=REMOTE_APPROVAL_CHOICES,
    help="Remote tool approval policy for the created session.",
)
@click.option(
    "--yes",
    is_flag=True,
    help="When used with --download, overwrite the local workspace without confirmation.",
)
@click.option(
    "--download",
    "download_results",
    is_flag=True,
    help="Download and overwrite --repo after the remote run completes.",
)
def remote_run(
    name: str,
    repo: str | None,
    empty_workspace: bool,
    runtime_profile: str | None,
    goal: str,
    approval_policy: str,
    yes: bool,
    download_results: bool,
) -> None:
    """Create a one-shot remote run and stream one prompt."""
    _remote_run_once(
        name=name,
        repo=repo,
        empty_workspace=empty_workspace,
        runtime_profile=runtime_profile,
        goal=goal,
        approval_policy=approval_policy,
        yes=yes,
        download_results=download_results,
        cleanup_on_success=download_results,
    )


def _remote_run_once(
    *,
    name: str,
    repo: str | None,
    empty_workspace: bool,
    runtime_profile: str | None,
    goal: str,
    approval_policy: str,
    yes: bool,
    download_results: bool,
    cleanup_on_success: bool,
) -> None:
    from coding_agent.remote.client import (
        auth_headers,
        create_remote_session,
        delete_remote_session,
        get_remote,
        stream_prompt,
    )
    from coding_agent.workspace_archive import (
        create_workspace_archive_base64,
    )

    if repo is not None and empty_workspace:
        raise click.ClickException(
            "Pass either --repo to upload a workspace snapshot or --empty-workspace, not both."
        )
    if repo is None and not empty_workspace:
        raise click.ClickException(
            "Pass --repo to upload a workspace snapshot or --empty-workspace to create a blank remote workspace."
        )
    if download_results and repo is None:
        raise click.ClickException("Pass --repo with --download.")

    endpoint = get_remote(name)
    headers = auth_headers(endpoint)
    repo_path: Path | None = None
    snapshot_archive_base64: str | None = None
    if repo is not None:
        repo_path = Path(repo).expanduser().resolve()
        if not repo_path.is_dir():
            raise click.ClickException(f"--repo must be an existing directory: {repo}")
        try:
            snapshot_archive_base64 = create_workspace_archive_base64(repo_path)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

    session_id = create_remote_session(
        endpoint,
        snapshot_archive_base64=snapshot_archive_base64,
        approval_policy=approval_policy,
        runtime_profile=runtime_profile,
    )
    click.echo(f"Created one-shot remote session {session_id} on remote {name}")
    status: int | None = None
    stream_error: Exception | None = None
    deferred_error: click.ClickException | None = None
    workspace_restore_failed = False
    try:
        status = stream_prompt(
            base_url=endpoint.url,
            session_id=session_id,
            prompt=goal,
            headers=headers,
        )
    except Exception as exc:
        stream_error = exc
    finally:
        if repo_path is not None and download_results:
            try:
                _download_and_restore_workspace(
                    base_url=endpoint.url,
                    session_id=session_id,
                    headers=headers,
                    repo_path=repo_path,
                    yes=yes,
                )
            except Exception as exc:
                workspace_restore_failed = True
                if stream_error is not None:
                    stream_error.add_note(f"Workspace download also failed: {exc}")
                else:
                    deferred_error = click.ClickException(str(exc))
        if workspace_restore_failed:
            click.echo(
                f"Remote session {session_id} left open; local workspace restore failed."
            )
            if repo_path is not None:
                click.echo(
                    "Retry download with: "
                    + _format_cli_command(
                        [
                            "python",
                            "-m",
                            "coding_agent",
                            "remote",
                            "download",
                            name,
                            "--session",
                            session_id,
                            "--repo",
                            str(repo_path),
                        ]
                    )
                )
            click.echo(
                "Continue prompting with: "
                + _format_cli_command(
                    [
                        "python",
                        "-m",
                        "coding_agent",
                        "attach",
                        name,
                        "--session",
                        session_id,
                        "--goal",
                        "<goal>",
                    ]
                )
            )
        elif cleanup_on_success:
            try:
                delete_remote_session(
                    base_url=endpoint.url,
                    session_id=session_id,
                    headers=headers,
                )
                click.echo(f"Cleaned up remote session {session_id}")
            except Exception as exc:
                if stream_error is not None:
                    stream_error.add_note(f"Remote session cleanup also failed: {exc}")
                elif deferred_error is not None:
                    deferred_error.add_note(
                        f"Remote session cleanup also failed: {exc}"
                    )
                else:
                    raise
        else:
            _print_remote_result_next_steps(
                remote_name=name,
                session_id=session_id,
                repo_path=repo_path,
            )
    if stream_error is not None:
        raise stream_error
    if deferred_error is not None:
        raise deferred_error
    assert status is not None
    raise SystemExit(status)


def _format_cli_command(args: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in args)


def _print_remote_result_next_steps(
    *,
    remote_name: str,
    session_id: str,
    repo_path: Path | None,
) -> None:
    click.echo(f"Remote session {session_id} left open for result inspection.")
    click.echo(
        "Show changed files: "
        + _format_cli_command(
            [
                "python",
                "-m",
                "coding_agent",
                "remote",
                "diff",
                remote_name,
                "--session",
                session_id,
            ]
        )
    )
    click.echo(
        "Export patch: "
        + _format_cli_command(
            [
                "python",
                "-m",
                "coding_agent",
                "remote",
                "patch",
                remote_name,
                "--session",
                session_id,
            ]
        )
    )
    if repo_path is not None:
        click.echo(
            "Fallback archive download: "
            + _format_cli_command(
                [
                    "python",
                    "-m",
                    "coding_agent",
                    "remote",
                    "download",
                    remote_name,
                    "--session",
                    session_id,
                    "--repo",
                    str(repo_path),
                ]
            )
        )
    click.echo(
        "Close session: "
        + _format_cli_command(
            [
                "python",
                "-m",
                "coding_agent",
                "remote",
                "sessions",
                "close",
                remote_name,
                session_id,
            ]
        )
    )


@remote.group("sessions")
def remote_sessions() -> None:
    """Inspect and control remote sessions."""


@remote_sessions.command("list")
@click.argument("name")
def remote_sessions_list(name: str) -> None:
    """List sessions visible to the remote token."""
    from coding_agent.remote.client import get_remote, list_remote_sessions

    endpoint = get_remote(name)
    sessions = list_remote_sessions(endpoint)
    if not sessions:
        click.echo("No remote sessions found.")
        return
    for session in sessions:
        click.echo(
            "\t".join(
                [
                    str(session.get("session_id", "")),
                    str(session.get("status", "")),
                    str(session.get("turn_status", "")),
                    str(session.get("workspace_id", "")),
                ]
            )
        )


@remote_sessions.command("status")
@click.argument("name")
@click.argument("session_id")
def remote_sessions_status(name: str, session_id: str) -> None:
    """Show one remote session."""
    from coding_agent.remote.client import get_remote, get_remote_session

    endpoint = get_remote(name)
    session = get_remote_session(endpoint, session_id)
    for key in sorted(session):
        click.echo(f"{key}: {session[key]}")


@remote_sessions.command("cancel")
@click.argument("name")
@click.argument("session_id")
def remote_sessions_cancel(name: str, session_id: str) -> None:
    """Cancel the active turn for a remote session."""
    from coding_agent.remote.client import cancel_remote_session, get_remote

    endpoint = get_remote(name)
    result = cancel_remote_session(endpoint, session_id)
    turn_id = result.get("turn_id")
    if isinstance(turn_id, str) and turn_id:
        click.echo(f"Cancelling remote session {session_id} turn {turn_id}")
        return
    click.echo(f"Cancelling remote session {session_id}")


@remote_sessions.command("close")
@click.argument("name")
@click.argument("session_id")
def remote_sessions_close(name: str, session_id: str) -> None:
    """Close a remote session."""
    from coding_agent.remote.client import (
        auth_headers,
        delete_remote_session,
        get_remote,
    )

    endpoint = get_remote(name)
    delete_remote_session(
        base_url=endpoint.url,
        session_id=session_id,
        headers=auth_headers(endpoint),
    )
    click.echo(f"Closed remote session {session_id}")


@remote.group("workspaces")
def remote_workspaces() -> None:
    """Inspect and clean remote workspaces."""


@remote_workspaces.command("list")
@click.argument("name")
def remote_workspaces_list(name: str) -> None:
    """List remote workspaces."""
    from coding_agent.remote.client import get_remote, list_remote_workspaces

    endpoint = get_remote(name)
    workspaces = list_remote_workspaces(endpoint)
    if not workspaces:
        click.echo("No remote workspaces found.")
        return
    for workspace in workspaces:
        click.echo(
            "\t".join(
                [
                    str(workspace.get("workspace_id", "")),
                    str(workspace.get("status", "")),
                    str(workspace.get("updated_at", "")),
                ]
            )
        )


@remote_workspaces.command("cleanup")
@click.argument("name")
@click.option("--stale", is_flag=True, help="Run server-side stale workspace GC.")
def remote_workspaces_cleanup(name: str, stale: bool) -> None:
    """Clean stale remote workspaces."""
    from coding_agent.remote.client import cleanup_stale_remote_workspaces, get_remote

    if not stale:
        raise click.ClickException("Pass --stale to run stale workspace cleanup.")
    endpoint = get_remote(name)
    cleaned_count = cleanup_stale_remote_workspaces(endpoint)
    click.echo(f"Cleaned {cleaned_count} stale workspaces")


@remote_workspaces.command("rm")
@click.argument("name")
@click.argument("workspace_id")
def remote_workspaces_rm(name: str, workspace_id: str) -> None:
    """Clean one remote workspace by id."""
    from coding_agent.remote.client import cleanup_remote_workspace, get_remote

    endpoint = get_remote(name)
    _ = cleanup_remote_workspace(endpoint, workspace_id)
    click.echo(f"Cleaned workspace {workspace_id}")


@remote.command("diff")
@click.argument("name")
@click.option("--session", "session_id", required=True, help="Remote session ID")
def remote_diff(name: str, session_id: str) -> None:
    """Show changed files for a remote session workspace."""
    from coding_agent.remote.client import download_workspace_diff, get_remote

    endpoint = get_remote(name)
    diff = download_workspace_diff(endpoint, session_id)
    files = diff.get("files")
    if not isinstance(files, list):
        raise click.ClickException("Remote workspace diff response missing files")
    workspace_id = diff.get("workspace_id")
    workspace_label = workspace_id if isinstance(workspace_id, str) else "unknown"
    additions = diff.get("additions")
    deletions = diff.get("deletions")
    if not isinstance(additions, int) or not isinstance(deletions, int):
        raise click.ClickException("Remote workspace diff response missing totals")

    click.echo(
        f"Remote workspace {workspace_label}: {len(files)} files changed, +{additions}/-{deletions}"
    )
    for raw_file in files:
        if not isinstance(raw_file, dict):
            raise click.ClickException("Remote workspace diff file entry is invalid")
        path = raw_file.get("path")
        status = raw_file.get("status")
        if not isinstance(path, str) or not isinstance(status, str):
            raise click.ClickException("Remote workspace diff file entry is invalid")
        old_path = raw_file.get("old_path")
        display_path = (
            f"{old_path} -> {path}" if isinstance(old_path, str) and old_path else path
        )
        if raw_file.get("binary") is True:
            change_summary = "binary"
        else:
            file_additions = raw_file.get("additions")
            file_deletions = raw_file.get("deletions")
            if isinstance(file_additions, int) and isinstance(file_deletions, int):
                change_summary = f"+{file_additions}/-{file_deletions}"
            else:
                change_summary = "+?/-?"
        click.echo(f"{status.ljust(9)} {display_path}  {change_summary}")


@remote.command("patch")
@click.argument("name")
@click.option("--session", "session_id", required=True, help="Remote session ID")
def remote_patch(name: str, session_id: str) -> None:
    """Print a unified patch for a remote session workspace."""
    from coding_agent.remote.client import download_workspace_patch, get_remote

    endpoint = get_remote(name)
    patch = download_workspace_patch(endpoint, session_id)
    click.echo(patch, nl=False)


@remote.command("publish")
@click.argument("name")
@click.option("--session", "session_id", required=True, help="Remote session ID")
@click.option(
    "--branch",
    "branch_name",
    required=True,
    help="Publish the remote session changes to this Git branch.",
)
def remote_publish(name: str, session_id: str, branch_name: str) -> None:
    """Publish remote session results as a Git branch."""
    from coding_agent.remote.client import get_remote, publish_remote_branch

    endpoint = get_remote(name)
    publication = publish_remote_branch(endpoint, session_id, branch_name)
    published_branch = publication.get("branch_name")
    pushed_ref = publication.get("pushed_ref")
    commit_sha = publication.get("commit_sha")
    remote_url = publication.get("remote_url")
    if not isinstance(published_branch, str) or not published_branch:
        raise click.ClickException("Remote publish response missing branch_name")
    if not isinstance(commit_sha, str) or not commit_sha:
        raise click.ClickException("Remote publish response missing commit_sha")
    if not isinstance(remote_url, str) or not remote_url:
        raise click.ClickException("Remote publish response missing remote_url")
    click.echo(f"Published branch {published_branch}")
    if isinstance(pushed_ref, str) and pushed_ref:
        click.echo(f"Ref: {pushed_ref}")
    click.echo(f"Commit: {commit_sha}")
    click.echo(f"Remote: {remote_url}")


@remote.command("download")
@click.argument("name")
@click.option("--session", "session_id", required=True, help="Remote session ID")
@click.option(
    "--repo",
    default=".",
    help="Local repo/workspace to overwrite with the remote snapshot.",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Download and overwrite the local workspace without confirmation.",
)
def remote_download(name: str, session_id: str, repo: str, yes: bool) -> None:
    """Download a remote session workspace snapshot."""
    from coding_agent.remote.client import auth_headers, get_remote

    endpoint = get_remote(name)
    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.is_dir():
        raise click.ClickException(f"--repo must be an existing directory: {repo}")
    _download_and_restore_workspace(
        base_url=endpoint.url,
        session_id=session_id,
        headers=auth_headers(endpoint),
        repo_path=repo_path,
        yes=yes,
    )


def _download_and_restore_workspace(
    *,
    base_url: str,
    session_id: str,
    headers: dict[str, str],
    repo_path: Path,
    yes: bool,
) -> None:
    from coding_agent.remote.client import (
        download_workspace_archive,
        download_workspace_manifest,
    )
    from coding_agent.workspace_archive import extract_workspace_archive_base64

    manifest = download_workspace_manifest(
        base_url=base_url,
        session_id=session_id,
        headers=headers,
    )
    changed_count = _manifest_file_count(manifest, "changed_files")
    deleted_count = _manifest_file_count(manifest, "deleted_files")
    total = manifest.get("total_bytes")
    if not isinstance(total, int):
        raise click.ClickException("Remote workspace manifest missing total_bytes")
    click.echo(
        f"Remote snapshot contains {changed_count} archived files, {deleted_count} deleted entries, {total} bytes"
    )
    click.echo(f"This will overwrite {repo_path} while preserving .git.")
    if not yes and not click.confirm("Continue?", default=False):
        raise click.ClickException("Remote workspace download cancelled.")
    archive_base64 = download_workspace_archive(
        base_url=base_url,
        session_id=session_id,
        headers=headers,
    )
    extract_workspace_archive_base64(repo_path, archive_base64)
    click.echo(
        f"Downloaded remote workspace snapshot and overwrote {repo_path} while preserving .git"
    )


def _manifest_file_count(manifest: dict[str, object], field: str) -> int:
    values = manifest.get(field)
    if not isinstance(values, list):
        raise click.ClickException(f"Remote workspace manifest missing {field}")
    values = cast(list[object], values)
    return len(values)


@main.command()
@click.argument("name")
@click.option("--session", "session_id", required=True, help="Remote session ID")
@click.option("--goal", required=True, help="Prompt to send to the remote session")
def attach(name: str, session_id: str, goal: str) -> None:
    """Send one prompt to an existing remote session."""
    from coding_agent.remote.client import auth_headers, get_remote, stream_prompt

    endpoint = get_remote(name)
    raise SystemExit(
        stream_prompt(
            base_url=endpoint.url,
            session_id=session_id,
            prompt=goal,
            headers=auth_headers(endpoint),
        )
    )


@main.command()
@click.option(
    "--task-packet",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to the task packet markdown file.",
)
@click.option(
    "--mode",
    default="run",
    type=click.Choice(["run", "checklist"]),
    help="Whether to execute verification or print a human checklist.",
)
@click.option(
    "--repo",
    default=Path("."),
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Repository root used as the working directory for verification commands.",
)
def verify(task_packet: Path, mode: str, repo: Path) -> None:
    """Verify a task packet or print its checklist."""
    try:
        contract = load_task_packet_contract(task_packet)
    except ValueError as exc:
        raise click.ClickException(f"Invalid task packet: {exc}") from exc
    runner = VerificationRunner()

    if mode == "checklist":
        click.echo(runner.render_checklist(contract).text)
        return

    report = runner.run(contract, repo_root=repo)
    for step in report.steps:
        status = "PASS" if step.passed else "FAIL"
        click.echo(f"[{status}] {step.name}")
        click.echo(f"  $ {step.command}")
        if step.stdout:
            click.echo(f"  stdout: {step.stdout.rstrip()}")
        if step.stderr:
            click.echo(f"  stderr: {step.stderr.rstrip()}")
        if not step.passed:
            click.echo(f"  exit_code: {step.exit_code}")
    click.echo(report.verdict)
    if report.verdict != "VERIFIED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
