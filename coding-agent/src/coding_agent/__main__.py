"""CLI entry point: python -m coding_agent"""

import click


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
@click.option("--provider", "provider_name", default="openai", type=click.Choice(["openai", "anthropic"]))
@click.option("--base-url", default=None, help="OpenAI-compatible API base URL")
@click.option("--api-key", envvar="AGENT_API_KEY", required=True, help="API key")
@click.option("--max-steps", default=30, help="Max steps per turn")
@click.option("--approval", default="yolo", type=click.Choice(["yolo", "interactive", "auto"]))
@click.option("--tui", is_flag=True, help="Use Rich TUI interface (batch mode)")
def run(goal, repo, model, provider_name, base_url, api_key, max_steps, approval, tui):
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
    )
    
    if tui:
        asyncio.run(_run_with_tui(config, goal))
    else:
        asyncio.run(_run_headless(config, goal))


@main.command()
@click.option("--repo", default=".", help="Repository path")
@click.option("--model", default="gpt-4o", help="Model name")
@click.option("--provider", "provider_name", default="openai", type=click.Choice(["openai", "anthropic"]))
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
    from coding_agent.core.loop import AgentLoop
    from coding_agent.core.planner import PlanManager
    from coding_agent.tools.registry import ToolRegistry
    from coding_agent.tools.file import register_file_tools
    from coding_agent.tools.shell import register_shell_tools
    from coding_agent.tools.search import register_search_tools
    from coding_agent.tools.planner import register_planner_tools
    from coding_agent.tools.subagent import register_subagent_tool
    from coding_agent.core.tape import Tape
    from coding_agent.core.context import Context
    from coding_agent.ui.rich_tui import CodingAgentTUI

    tape = Tape.create(config.tape_dir)
    provider = _create_provider(config)

    planner = PlanManager()
    registry = ToolRegistry()
    register_file_tools(registry, repo_root=config.repo)
    register_shell_tools(registry, cwd=config.repo)
    register_search_tools(registry, repo_root=config.repo)
    register_planner_tools(registry, planner)

    tui = CodingAgentTUI(model_name=config.model, max_steps=config.max_steps)
    consumer = tui.consumer
    
    # Register subagent tool
    register_subagent_tool(
        registry=registry,
        provider=provider,
        tape=tape,
        consumer=consumer,
        max_steps=config.subagent_max_steps,
        max_depth=config.max_subagent_depth,
    )
    
    system_prompt = (
        "You are a coding agent. You can read files, edit files, "
        "run shell commands, search the codebase, create task plans, "
        "and dispatch sub-agents for independent sub-tasks.\n\n"
        "Always create a plan (todo_write) before starting complex work. "
        "Update task status as you progress."
    )
    context = Context(provider.max_context_size, system_prompt, planner=planner)
    
    loop = AgentLoop(
        provider=provider,
        tools=registry,
        tape=tape,
        context=context,
        consumer=consumer,
        max_steps=config.max_steps,
    )
    
    with tui:
        tui.add_user_message(goal)
        result = await loop.run_turn(goal)
        click.echo(f"\n--- Result ({result.stop_reason}) ---")


async def _run_headless(config, goal):
    """Run agent in headless mode."""
    from coding_agent.core.loop import AgentLoop
    from coding_agent.core.planner import PlanManager
    from coding_agent.tools.registry import ToolRegistry
    from coding_agent.tools.file import register_file_tools
    from coding_agent.tools.shell import register_shell_tools
    from coding_agent.tools.search import register_search_tools
    from coding_agent.tools.planner import register_planner_tools
    from coding_agent.tools.subagent import register_subagent_tool
    from coding_agent.core.tape import Tape
    from coding_agent.core.context import Context
    from coding_agent.ui.headless import HeadlessConsumer

    tape = Tape.create(config.tape_dir)
    provider = _create_provider(config)

    planner = PlanManager()
    registry = ToolRegistry()
    register_file_tools(registry, repo_root=config.repo)
    register_shell_tools(registry, cwd=config.repo)
    register_search_tools(registry, repo_root=config.repo)
    register_planner_tools(registry, planner)

    consumer = HeadlessConsumer()

    # Register subagent tool (needs provider, tape, consumer)
    register_subagent_tool(
        registry=registry,
        provider=provider,
        tape=tape,
        consumer=consumer,
        max_steps=config.subagent_max_steps,
        max_depth=config.max_subagent_depth,
    )

    system_prompt = (
        "You are a coding agent. You can read files, edit files, "
        "run shell commands, search the codebase, create task plans, "
        "and dispatch sub-agents for independent sub-tasks.\n\n"
        "Always create a plan (todo_write) before starting complex work. "
        "Update task status as you progress."
    )
    context = Context(provider.max_context_size, system_prompt, planner=planner)

    loop = AgentLoop(
        provider=provider,
        tools=registry,
        tape=tape,
        context=context,
        consumer=consumer,
        max_steps=config.max_steps,
    )

    result = await loop.run_turn(goal)
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


if __name__ == "__main__":
    main()
