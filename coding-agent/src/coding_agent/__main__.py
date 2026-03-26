"""CLI entry point: python -m coding_agent"""

import click


@click.group()
def main():
    """Coding Agent CLI."""
    pass


@main.command()
@click.option("--goal", required=True, help="Task goal for the agent")
@click.option("--repo", default=".", help="Repository path")
@click.option("--model", default="gpt-4o", help="Model name")
@click.option("--base-url", default=None, help="OpenAI-compatible API base URL")
@click.option("--api-key", envvar="AGENT_API_KEY", required=True, help="API key")
@click.option("--max-steps", default=30, help="Max steps per turn")
@click.option("--approval", default="yolo", type=click.Choice(["yolo", "interactive", "auto"]))
def run(goal, repo, model, base_url, api_key, max_steps, approval):
    """Run agent on a goal (batch mode)."""
    import asyncio
    from coding_agent.core.config import Config

    config = Config(
        model=model,
        api_key=api_key,
        base_url=base_url,
        repo=repo,
        max_steps=max_steps,
        approval_mode=approval,
    )
    asyncio.run(_run(config, goal))


async def _run(config, goal):
    from coding_agent.core.loop import AgentLoop
    from coding_agent.providers.openai_compat import OpenAICompatProvider
    from coding_agent.tools.registry import ToolRegistry
    from coding_agent.tools.file import register_file_tools
    from coding_agent.tools.shell import register_shell_tools
    from coding_agent.tools.search import register_search_tools
    from coding_agent.core.tape import Tape
    from coding_agent.core.context import Context
    from coding_agent.ui.headless import HeadlessConsumer  # noqa: WireConsumer impl

    tape = Tape.create(config.tape_dir)
    provider = OpenAICompatProvider(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
    )
    registry = ToolRegistry()
    register_file_tools(registry, repo_root=config.repo)
    register_shell_tools(registry, cwd=config.repo)
    register_search_tools(registry, repo_root=config.repo)

    system_prompt = (
        "You are a coding agent. You can read files, edit files, "
        "run shell commands, and search the codebase to accomplish tasks."
    )
    context = Context(provider.max_context_size, system_prompt)
    consumer = HeadlessConsumer()

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


if __name__ == "__main__":
    main()
