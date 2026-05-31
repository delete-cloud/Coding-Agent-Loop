"""CLI entry point: python -m coding_agent"""

from __future__ import annotations

import sys
from typing import get_args

import click

from coding_agent.adapter import PipelineAdapter
from coding_agent.app import create_agent, create_child_pipeline  # noqa: F401
from coding_agent.cli.kb_commands import kb
from coding_agent.cli.oauth_commands import oauth_cli
from coding_agent.cli.postmortem_commands import postmortem
from coding_agent.cli.remote_commands import attach, remote
from coding_agent.cli.serve_command import serve
from coding_agent.cli.stats_command import stats
from coding_agent.cli.verify_command import verify
from coding_agent.core.config import Config, load_config
from coding_agent.remote.approval import APPROVAL_POLICIES
from coding_agent.ui.headless import HeadlessConsumer
from coding_agent.ui.rich_tui import CodingAgentTUI


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


main.add_command(kb)
main.add_command(oauth_cli)
main.add_command(postmortem)
main.add_command(stats)
main.add_command(serve)
main.add_command(remote)
main.add_command(attach)
main.add_command(verify)


if __name__ == "__main__":
    main()
