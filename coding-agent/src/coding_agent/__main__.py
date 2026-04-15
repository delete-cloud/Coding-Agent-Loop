"""CLI entry point: python -m coding_agent"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, get_args

import click

from coding_agent.adapter import PipelineAdapter
from coding_agent.core.config import Config, load_config
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


@main.command()
@click.option("--goal", required=True, help="Task goal for the agent")
@click.option("--repo", default=".", help="Repository path")
@click.option("--max-steps", default=30, help="Max steps per turn")
@click.option(
    "--approval", default="yolo", type=click.Choice(["yolo", "interactive", "auto"])
)
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

    click.echo(f"Session: {data['session_id']}")
    click.echo(f"Duration: {data['duration']}")
    click.echo(f"\nTools: {data['tools_total']} calls")
    for tool, count in data["tool_calls"].items():
        click.echo(f"  • {tool}: {count}")
    click.echo(
        f"\nAPI: {data['api_calls']} calls, avg latency {data['avg_api_latency']}"
    )
    click.echo(f"Cache hit rate: {data['cache_hit_rate']}")
    click.echo(f"Tokens: {data['tokens_input']} in / {data['tokens_output']} out")


@main.command()
@click.option("--port", default=8080, help="Server port")
@click.option("--host", default="127.0.0.1", help="Server host")
def serve(port: int, host: str):
    """Start HTTP API server."""
    import uvicorn
    from coding_agent.ui.http_server import app

    click.echo(f"Starting Coding Agent HTTP server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


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
def verify(task_packet: Path, mode: str) -> None:
    """Verify a task packet or print its checklist."""
    try:
        contract = load_task_packet_contract(task_packet)
    except ValueError as exc:
        raise click.ClickException(f"Invalid task packet: {exc}") from exc
    runner = VerificationRunner()

    if mode == "checklist":
        click.echo(runner.render_checklist(contract).text)
        return

    report = runner.run(contract)
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
