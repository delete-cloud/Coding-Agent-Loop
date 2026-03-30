"""CLI entry point: python -m coding_agent"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import click

from coding_agent.adapter import PipelineAdapter
from coding_agent.ui.headless import HeadlessConsumer
from coding_agent.ui.rich_tui import CodingAgentTUI


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
) -> tuple:
    """Create a fully wired agent from config.

    Returns (Pipeline, PipelineContext) ready for run_turn().
    """
    import os

    from agentkit.config.loader import AgentConfig, load_config
    from agentkit.directive.executor import DirectiveExecutor
    from agentkit.plugin.registry import PluginRegistry
    from agentkit.runtime.hook_runtime import HookRuntime
    from agentkit.runtime.pipeline import Pipeline, PipelineContext
    from agentkit.tape.tape import Tape
    from coding_agent.plugins.shell_session import ShellSessionPlugin

    if config_path is None:
        config_path = Path(__file__).parent / "agent.toml"
    if data_dir is None:
        data_dir = Path("./data")

    workspace_root = workspace_root or Path.cwd()

    cfg = load_config(config_path)

    if model_override:
        cfg.model = model_override
    if provider_override:
        cfg.provider = provider_override
    if max_steps_override is not None:
        cfg.max_turns = max_steps_override

    resolved_key = api_key or os.environ.get("AGENT_API_KEY", "")

    registry = PluginRegistry()
    shell_session = ShellSessionPlugin()
    plugin_factories = {
        "llm_provider": lambda: LLMProviderPlugin(
            provider=cfg.provider,
            model=cfg.model,
            api_key=resolved_key,
            base_url=base_url_override,
        ),
        "storage": lambda: StoragePlugin(data_dir=data_dir),
        "core_tools": lambda: CoreToolsPlugin(
            workspace_root=workspace_root,
            shell_session=shell_session,
        ),
        "approval": lambda: ApprovalPlugin(
            policy=policy,
            blocked_tools=set(approval_cfg.get("blocked_tools", [])),
        ),
        "summarizer": lambda: SummarizerPlugin(
            max_entries=sum_cfg.get("max_entries", 100),
            keep_recent=sum_cfg.get("keep_recent", 20),
        ),
        "memory": lambda: MemoryPlugin(),
        "shell_session": lambda: shell_session,
    }

    from coding_agent.plugins.approval import ApprovalPlugin, ApprovalPolicy
    from coding_agent.plugins.core_tools import CoreToolsPlugin
    from coding_agent.plugins.doom_detector import DoomDetectorPlugin
    from coding_agent.plugins.llm_provider import LLMProviderPlugin
    from coding_agent.plugins.memory import MemoryPlugin
    from coding_agent.plugins.metrics import SessionMetricsPlugin
    from coding_agent.plugins.parallel_executor import ParallelExecutorPlugin
    from coding_agent.plugins.storage import StoragePlugin
    from coding_agent.plugins.summarizer import SummarizerPlugin

    approval_cfg = cfg.extra.get("approval", {})
    policy_str = approval_mode_override or approval_cfg.get("policy", "auto")
    approval_policy_map = {
        "yolo": ApprovalPolicy.AUTO,
        "interactive": ApprovalPolicy.MANUAL,
        "auto": ApprovalPolicy.AUTO,
    }
    policy = approval_policy_map.get(policy_str)
    if policy is None:
        raise ValueError(f"unsupported approval policy: {policy_str}")

    sum_cfg = cfg.extra.get("summarizer", {})
    parallel_cfg = cfg.extra.get("parallel", {})
    doom_cfg = cfg.extra.get("doom_detector", {})

    async def _execute_tool_async(name: str, arguments: dict[str, Any]) -> str:
        core_tools = registry.get("core_tools")
        result = await core_tools.execute_tool_async(name=name, arguments=arguments)
        return str(result) if result is not None else ""

    plugin_factories.update(
        {
            "doom_detector": lambda: DoomDetectorPlugin(
                threshold=int(doom_cfg.get("threshold", 3))
            ),
            "parallel_executor": lambda: ParallelExecutorPlugin(
                execute_fn=_execute_tool_async,
                max_concurrency=int(parallel_cfg.get("max_concurrency", 5)),
            ),
            "session_metrics": lambda: SessionMetricsPlugin(),
        }
    )

    enabled_plugins = cfg.plugins or list(plugin_factories.keys())
    for plugin_name in enabled_plugins:
        factory = plugin_factories.get(plugin_name)
        if factory is None:
            raise ValueError(f"unsupported plugin in config: {plugin_name}")
        registry.register(factory())

    runtime = HookRuntime(registry)

    directive_executor = DirectiveExecutor()

    pipeline = Pipeline(
        runtime=runtime,
        registry=registry,
        directive_executor=directive_executor,
    )

    ctx = PipelineContext(
        tape=Tape(),
        session_id=session_id_override or uuid.uuid4().hex,
        config={
            "system_prompt": cfg.system_prompt,
            "model": cfg.model,
            "provider": cfg.provider,
            "max_tool_rounds": cfg.max_turns,
        },
    )

    return pipeline, ctx


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx):
    """Coding Agent CLI.

    Without subcommand: starts interactive REPL mode (default)
    """
    if ctx.invoked_subcommand is None:
        # Default to interactive REPL mode
        import asyncio
        from coding_agent.cli.repl import run_repl
        from coding_agent.core.config import load_config

        config = load_config()
        asyncio.run(run_repl(config))


@main.command()
@click.option("--goal", required=True, help="Task goal for the agent")
@click.option("--repo", default=".", help="Repository path")
@click.option("--model", default="gpt-4o", help="Model name")
@click.option(
    "--provider",
    "provider_name",
    default="openai",
    type=click.Choice(["openai", "anthropic"]),
)
@click.option("--base-url", default=None, help="OpenAI-compatible API base URL")
@click.option("--api-key", envvar="AGENT_API_KEY", required=True, help="API key")
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
    from coding_agent.core.config import Config

    config = Config(
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
    type=click.Choice(["openai", "anthropic"]),
)
@click.option("--base-url", default=None, help="OpenAI-compatible API base URL")
@click.option("--api-key", envvar="AGENT_API_KEY", required=True, help="API key")
@click.option("--max-steps", default=30, help="Max steps per turn")
def repl(repo, model, provider_name, base_url, api_key, max_steps):
    """Start interactive REPL mode (explicit)."""
    import asyncio
    from coding_agent.cli.repl import run_repl
    from coding_agent.core.config import Config

    config = Config(
        provider=provider_name,
        model=model,
        api_key=api_key,
        base_url=base_url,
        repo=repo,
        max_steps=max_steps,
        approval_mode="yolo",
    )
    asyncio.run(run_repl(config))


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


if __name__ == "__main__":
    main()
