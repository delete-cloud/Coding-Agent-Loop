"""CLI entry point: python -m coding_agent"""

from __future__ import annotations

import os
import uuid
from importlib import import_module
from pathlib import Path
from typing import Any

import click

from coding_agent.adapter import PipelineAdapter
from coding_agent.ui.headless import HeadlessConsumer


PROVIDER_CHOICES = [
    "openai",
    "anthropic",
    "copilot",
    "kimi",
    "kimi-code",
    "kimi-code-anthropic",
]


def create_agent(
    config_path: Path | None = None,
    data_dir: Path | None = None,
    api_key: str | None = None,
    model_override: str | None = None,
    provider_override: str | None = None,
    base_url_override: str | None = None,
    workspace_root: Path | None = None,
    max_steps_override: int | None = None,
    approval_mode_override: str | None = None,
    session_id_override: str | None = None,
) -> tuple[Any, Any]:
    return import_module("coding_agent.app").create_agent(
        config_path=config_path,
        data_dir=data_dir,
        api_key=api_key,
        model_override=model_override,
        provider_override=provider_override,
        base_url_override=base_url_override,
        workspace_root=workspace_root,
        max_steps_override=max_steps_override,
        approval_mode_override=approval_mode_override,
        session_id_override=session_id_override,
    )


@click.group(invoke_without_command=True)
@click.option("--repo", default=None, help="Repository path")
@click.option("--model", default=None, help="Model name")
@click.option(
    "--provider",
    "provider_name",
    default=None,
    type=click.Choice(PROVIDER_CHOICES),
)
@click.option("--base-url", default=None, help="OpenAI-compatible API base URL")
@click.option("--api-key", envvar="AGENT_API_KEY", default=None, help="API key")
@click.option("--max-steps", default=None, type=int, help="Max steps per turn")
@click.pass_context
def main(ctx, repo, model, provider_name, base_url, api_key, max_steps):
    """Coding Agent CLI.

    Without subcommand: starts interactive REPL mode (default)
    """
    if ctx.invoked_subcommand is None:
        _run_repl_command(
            repo=repo,
            model=model,
            provider_name=provider_name,
            base_url=base_url,
            api_key=api_key,
            max_steps=max_steps,
        )


def _load_runtime_config(**cli_args):
    from coding_agent.core.config import load_config

    return load_config(cli_args=cli_args)


def _run_repl_command(
    *,
    repo=None,
    model=None,
    provider_name=None,
    base_url=None,
    api_key=None,
    max_steps=None,
):
    import asyncio
    from coding_agent.cli.repl import run_repl

    config = _load_runtime_config(
        repo=repo,
        model=model,
        provider=provider_name,
        base_url=base_url,
        api_key=api_key,
        max_steps=max_steps,
        approval_mode="yolo",
    )
    asyncio.run(run_repl(config))


@main.command()
@click.option("--goal", required=True, help="Task goal for the agent")
@click.option("--repo", default=".", help="Repository path")
@click.option("--model", default="gpt-4o", help="Model name")
@click.option(
    "--provider",
    "provider_name",
    default="openai",
    type=click.Choice(PROVIDER_CHOICES),
)
@click.option("--base-url", default=None, help="OpenAI-compatible API base URL")
@click.option("--api-key", envvar="AGENT_API_KEY", default=None, help="API key")
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
def run(
    goal,
    repo,
    model,
    provider_name,
    base_url,
    api_key,
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

    config = _load_runtime_config(
        provider=provider_name,
        model=model,
        api_key=api_key,
        base_url=base_url,
        repo=repo,
        max_steps=max_steps,
        approval_mode=approval,
        enable_parallel_tools=parallel,
        max_parallel_tools=max_parallel,
        enable_cache=cache,
        cache_size=cache_size,
    )

    if tui:
        asyncio.run(_run_with_tui(config, goal))
    else:
        asyncio.run(_run_headless(config, goal))


@main.command()
@click.option("--repo", default=".", help="Repository path")
@click.option("--model", default="gpt-4o", help="Model name")
@click.option(
    "--provider",
    "provider_name",
    default="openai",
    type=click.Choice(PROVIDER_CHOICES),
)
@click.option("--base-url", default=None, help="OpenAI-compatible API base URL")
@click.option("--api-key", envvar="AGENT_API_KEY", default=None, help="API key")
@click.option("--max-steps", default=30, help="Max steps per turn")
def repl(repo, model, provider_name, base_url, api_key, max_steps):
    """Start interactive REPL mode (explicit)."""
    _run_repl_command(
        repo=repo,
        model=model,
        provider_name=provider_name,
        base_url=base_url,
        api_key=api_key,
        max_steps=max_steps,
    )


async def _run_with_tui(config, goal):
    """Run agent with streaming TUI display."""
    from agentkit.tracing import configure_tracing

    from coding_agent.ui.rich_consumer import RichConsumer
    from coding_agent.ui.stream_renderer import StreamingRenderer

    configure_tracing()
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
    renderer = StreamingRenderer()
    consumer = RichConsumer(renderer)
    ctx.config["wire_consumer"] = consumer
    ctx.config["agent_id"] = ""
    adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)
    renderer.user_message(goal)
    try:
        result = await adapter.run_turn(goal)
    finally:
        await adapter.close()
    click.echo(f"\n--- Result ({result.stop_reason}) ---")


async def _run_headless(config, goal):
    """Run agent in headless mode."""
    from agentkit.tracing import configure_tracing

    configure_tracing()
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
    ctx.config["wire_consumer"] = consumer
    ctx.config["agent_id"] = ""
    adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)
    try:
        result = await adapter.run_turn(goal)
    finally:
        await adapter.close()
    click.echo(f"\n--- Result ({result.stop_reason}) ---")
    if result.final_message:
        click.echo(result.final_message)


@main.group()
def kb():
    pass


@kb.command("index")
@click.argument("path", type=click.Path(exists=True))
@click.option(
    "--db-path",
    default=None,
    help="LanceDB database path (default: from agent.toml [kb].db_path)",
)
def kb_index(path: str, db_path: str | None):
    import asyncio

    from agentkit.config.loader import load_config as load_agent_config

    from coding_agent.kb import KB

    root = Path(path)

    kb_cfg: dict[str, object] = {}
    config_path = Path(__file__).parent / "agent.toml"
    if config_path.exists():
        agent_cfg = load_agent_config(config_path)
        kb_cfg = agent_cfg.extra.get("kb", {})

    data_dir = Path(os.environ.get("AGENT_DATA_DIR", "./data"))
    resolved_db = (
        Path(db_path)
        if db_path is not None
        else data_dir / str(kb_cfg.get("db_path", "kb"))
    )

    raw_extensions = kb_cfg.get(
        "index_extensions",
        [".md", ".txt", ".rst", ".yaml", ".yml", ".toml"],
    )
    text_extensions = (
        set(raw_extensions)
        if isinstance(raw_extensions, list)
        else {".md", ".txt", ".rst", ".yaml", ".yml", ".toml"}
    )
    embedding_dim_raw = kb_cfg.get("embedding_dim", 1536)
    chunk_size_raw = kb_cfg.get("chunk_size", 1200)
    chunk_overlap_raw = kb_cfg.get("chunk_overlap", 200)
    embedding_dim = (
        int(embedding_dim_raw) if isinstance(embedding_dim_raw, int | str) else 1536
    )
    chunk_size = int(chunk_size_raw) if isinstance(chunk_size_raw, int | str) else 1200
    chunk_overlap = (
        int(chunk_overlap_raw) if isinstance(chunk_overlap_raw, int | str) else 200
    )

    probe_kb = KB(
        db_path=resolved_db,
        embedding_dim=embedding_dim,
        text_extensions=text_extensions,
    )
    if probe_kb.has_table():
        click.echo(
            "Chunks table already exists. Skipping. "
            "(Phase 1 does not support incremental updates.)"
        )
        return

    kb_instance = KB(
        db_path=resolved_db,
        embedding_model=str(kb_cfg.get("embedding_model", "text-embedding-3-small")),
        embedding_dim=embedding_dim,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        text_extensions=text_extensions,
    )

    asyncio.run(kb_instance.index_directory(root))

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

    kb_cfg: dict[str, object] = {}
    config_path = Path(__file__).parent / "agent.toml"
    if config_path.exists():
        from agentkit.config.loader import load_config as load_agent_config

        agent_cfg = load_agent_config(config_path)
        kb_cfg = agent_cfg.extra.get("kb", {})

    data_dir = Path(os.environ.get("AGENT_DATA_DIR", "./data"))
    resolved_db = (
        Path(db_path)
        if db_path is not None
        else data_dir / str(kb_cfg.get("db_path", "kb"))
    )

    embedding_dim_raw = kb_cfg.get("embedding_dim", 1536)
    embedding_dim = (
        int(embedding_dim_raw) if isinstance(embedding_dim_raw, int | str) else 1536
    )
    embedding_model = str(kb_cfg.get("embedding_model", "text-embedding-3-small"))

    kb_instance = KB(
        db_path=resolved_db,
        embedding_model=embedding_model,
        embedding_dim=embedding_dim,
    )

    if not kb_instance.has_table():
        click.echo("No index found. Run 'kb index <path>' first.")
        return

    results = kb_instance.search_sync(query, k=k)

    if not results:
        click.echo("No results found.")
        return

    for i, result in enumerate(results, 1):
        click.echo(f"\n--- Result {i} (score: {result.score:.4f}) ---")
        click.echo(f"Source: {result.chunk.source}")
        content = result.chunk.content
        if len(content) > 200:
            content = content[:200] + "..."
        click.echo(content)


def _create_provider(config):
    """Create the appropriate provider based on config."""
    if config.provider == "anthropic":
        from coding_agent.providers.anthropic import AnthropicProvider

        return AnthropicProvider(
            model=config.model,
            api_key=config.api_key,
        )
    if config.provider == "copilot":
        from coding_agent.providers.copilot import CopilotProvider

        return CopilotProvider(
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
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


if __name__ == "__main__":
    main()
