"""CLI entry point: python -m coding_agent"""

from __future__ import annotations

import inspect
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import click

from coding_agent.adapter import PipelineAdapter
from coding_agent.ui.headless import HeadlessConsumer
from coding_agent.ui.rich_tui import CodingAgentTUI

from agentkit.runtime.hookspecs import HOOK_SPECS
from agentkit.tape.tape import Tape

from coding_agent.approval import ApprovalPolicy
from coding_agent.plugins.approval import ApprovalPlugin
from coding_agent.plugins.core_tools import CoreToolsPlugin
from coding_agent.plugins.doom_detector import DoomDetectorPlugin
from coding_agent.plugins.kb import KBPlugin
from coding_agent.plugins.llm_provider import LLMProviderPlugin
from coding_agent.plugins.mcp import MCPPlugin
from coding_agent.plugins.memory import MemoryPlugin
from coding_agent.plugins.metrics import SessionMetricsPlugin
from coding_agent.plugins.parallel_executor import ParallelExecutorPlugin
from coding_agent.plugins.shell_session import ShellSessionPlugin
from coding_agent.plugins.skills import SkillsPlugin
from coding_agent.plugins.storage import StoragePlugin
from coding_agent.plugins.summarizer import SummarizerPlugin
from coding_agent.plugins.topic import TopicPlugin
from coding_agent.tools.web_search import create_web_search_backend

ToolFilter = Any


@contextmanager
def structured_tool_result_scope(enabled: bool):
    from coding_agent.tools.file_ops import structured_results_scope as file_ops_scope
    from coding_agent.tools.shell import structured_results_scope as shell_scope

    with file_ops_scope(enabled), shell_scope(enabled):
        yield


def _should_include_tool(tool_filter: ToolFilter, tool_name: str) -> bool:
    if tool_filter is None:
        return True
    if callable(tool_filter):
        return bool(tool_filter(tool_name))
    raise TypeError("tool_filter must be callable")


def _filter_core_tools_plugin(
    core_tools_plugin: CoreToolsPlugin,
    tool_filter: ToolFilter,
) -> None:
    if tool_filter is None:
        return

    registry = core_tools_plugin.registry
    allowed_names = [
        name for name in registry.names() if _should_include_tool(tool_filter, name)
    ]
    registry.retain(allowed_names)


def _build_llm_provider_plugin(
    *,
    provider: str,
    model: str,
    api_key: str,
    base_url: str | None,
    parent_provider: Any | None,
) -> LLMProviderPlugin:
    plugin = LLMProviderPlugin(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
    )
    if parent_provider is not None:
        plugin._instance = parent_provider
    return plugin


def create_child_pipeline(
    *,
    parent_provider: Any | None,
    tape_fork: Tape,
    tool_filter: ToolFilter = None,
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
    from agentkit.config.loader import load_config
    from agentkit.directive.executor import DirectiveExecutor
    from agentkit.plugin.registry import PluginRegistry
    from agentkit.runtime.hook_runtime import HookRuntime
    from agentkit.runtime.pipeline import Pipeline, PipelineContext

    if config_path is None:
        config_path = Path(__file__).parent / "agent.toml"
    if data_dir is None:
        data_dir = Path(os.environ.get("AGENT_DATA_DIR", "./data"))

    workspace_root = workspace_root or Path.cwd()

    cfg = load_config(config_path)

    if model_override:
        cfg.model = model_override
    if provider_override:
        cfg.provider = provider_override
    if max_steps_override is not None:
        cfg.max_turns = max_steps_override

    resolved_key = api_key or os.environ.get("AGENT_API_KEY")
    if not resolved_key and cfg.provider == "copilot":
        resolved_key = os.environ.get("GITHUB_TOKEN", "")
    if not resolved_key and cfg.provider == "kimi":
        resolved_key = os.environ.get("MOONSHOT_API_KEY", "")
    if not resolved_key and cfg.provider in ("kimi-code", "kimi-code-anthropic"):
        resolved_key = os.environ.get("KIMI_CODE_API_KEY", "")
    resolved_key = resolved_key or ""

    approval_cfg = cfg.extra.get("approval", {})
    subagent_cfg = cfg.extra.get("subagent", {})
    web_search_cfg = cfg.extra.get("web_search", {})
    shell_cfg = cfg.extra.get("shell", {})
    storage_cfg = cfg.extra.get("storage", {})
    sum_cfg = cfg.extra.get("summarizer", {})
    parallel_cfg = cfg.extra.get("parallel", {})
    doom_cfg = cfg.extra.get("doom_detector", {})
    topic_cfg = cfg.extra.get("topic", {})
    skills_cfg = cfg.extra.get("skills", {})
    mcp_cfg = cfg.extra.get("mcp", {})
    kb_cfg = cfg.extra.get("kb", {})

    policy_str = approval_mode_override or approval_cfg.get("policy", "auto")
    approval_policy_map = {
        "yolo": ApprovalPolicy.YOLO,
        "interactive": ApprovalPolicy.INTERACTIVE,
        "auto": ApprovalPolicy.AUTO,
    }
    policy = approval_policy_map.get(policy_str)
    if policy is None:
        raise ValueError(f"unsupported approval policy: {policy_str}")

    web_search_backend = create_web_search_backend(web_search_cfg)

    registry = PluginRegistry(specs=HOOK_SPECS)
    shell_session = ShellSessionPlugin()

    plugin_factories: dict[str, Any] = {
        "llm_provider": lambda: _build_llm_provider_plugin(
            provider=cfg.provider,
            model=cfg.model,
            api_key=resolved_key,
            base_url=base_url_override,
            parent_provider=parent_provider,
        ),
        "storage": lambda: StoragePlugin(data_dir=data_dir, config=storage_cfg),
        "core_tools": lambda: CoreToolsPlugin(
            workspace_root=workspace_root,
            shell_session=shell_session,
            web_search_backend=web_search_backend,
            child_pipeline_builder=create_child_pipeline,
        ),
        "approval": lambda: ApprovalPlugin(
            policy=policy,
            blocked_tools=set(approval_cfg.get("blocked_tools", [])),
            external_request_tools={"web_search"},
        ),
        "summarizer": lambda: SummarizerPlugin(
            max_entries=sum_cfg.get("max_entries", 100),
            keep_recent=sum_cfg.get("keep_recent", 20),
        ),
        "memory": lambda: MemoryPlugin(),
        "shell_session": lambda: shell_session,
        "doom_detector": lambda: DoomDetectorPlugin(
            threshold=int(doom_cfg.get("threshold", 3))
        ),
        "parallel_executor": lambda: ParallelExecutorPlugin(
            execute_fn=_execute_tool_async,
            max_concurrency=int(parallel_cfg.get("max_concurrency", 5)),
        ),
        "topic": lambda: TopicPlugin(
            overlap_threshold=float(topic_cfg.get("overlap_threshold", 0.2)),
            min_entries_before_detect=int(topic_cfg.get("min_entries", 4)),
        ),
        "session_metrics": lambda: SessionMetricsPlugin(),
        "skills": lambda: SkillsPlugin(
            workspace_root=workspace_root,
            extra_dirs=skills_cfg.get("extra_dirs", []),
        ),
        "mcp": lambda: MCPPlugin(servers=mcp_cfg.get("servers", {})),
        "kb": lambda: KBPlugin(
            db_path=data_dir / kb_cfg.get("db_path", "kb"),
            embedding_model=kb_cfg.get("embedding_model", "text-embedding-3-small"),
            embedding_dim=int(kb_cfg.get("embedding_dim", 1536)),
            chunk_size=int(kb_cfg.get("chunk_size", 1200)),
            chunk_overlap=int(kb_cfg.get("chunk_overlap", 200)),
            top_k=int(kb_cfg.get("top_k", 5)),
            text_extensions=kb_cfg.get(
                "index_extensions",
                [".md", ".txt", ".rst", ".yaml", ".yml", ".toml"],
            ),
        ),
    }

    async def _execute_tool_async(
        name: str,
        arguments: dict[str, Any],
        *,
        ctx: Any | None = None,
    ) -> Any:
        core_tools = registry.get("core_tools")
        if not isinstance(core_tools, CoreToolsPlugin):
            raise TypeError("core_tools plugin must be CoreToolsPlugin")
        execute_tool_async = core_tools.execute_tool_async
        signature = inspect.signature(execute_tool_async)
        accepts_ctx = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD or parameter.name == "ctx"
            for parameter in signature.parameters.values()
        )
        if accepts_ctx:
            return await execute_tool_async(name=name, arguments=arguments, ctx=ctx)
        return await execute_tool_async(name=name, arguments=arguments)

    enabled_plugins = cfg.plugins or list(plugin_factories.keys())
    for plugin_name in enabled_plugins:
        factory = plugin_factories.get(plugin_name)
        if factory is None:
            raise ValueError(f"unsupported plugin in config: {plugin_name}")
        plugin = factory()
        if isinstance(plugin, CoreToolsPlugin):
            _filter_core_tools_plugin(plugin, tool_filter)
        registry.register(plugin)

    runtime = HookRuntime(registry, specs=HOOK_SPECS)

    memory_plugin = None
    if "memory" in registry.plugin_ids():
        maybe_memory = registry.get("memory")
        if isinstance(maybe_memory, MemoryPlugin):
            memory_plugin = maybe_memory

    async def _memory_handler(directive: Any) -> None:
        if memory_plugin is not None:
            memory_plugin.add_memory(directive)

    directive_executor = DirectiveExecutor(
        memory_handler=_memory_handler if memory_plugin is not None else None,
    )

    pipeline = Pipeline(
        runtime=runtime,
        registry=registry,
        directive_executor=directive_executor,
    )

    ctx = PipelineContext(
        tape=tape_fork,
        session_id=session_id_override or uuid.uuid4().hex,
        config={
            "system_prompt": cfg.system_prompt,
            "model": cfg.model,
            "provider": cfg.provider,
            "max_tool_rounds": cfg.max_turns,
            "subagent_timeout": float(subagent_cfg.get("timeout", 30.0)),
            "web_search": web_search_cfg,
            "workspace_root": str(workspace_root),
            "shell": shell_cfg,
            "structured_tool_result_scope": structured_tool_result_scope,
        },
    )

    if "core_tools" in registry.plugin_ids():
        core_tools_plugin = registry.get("core_tools")
        if not isinstance(core_tools_plugin, CoreToolsPlugin):
            raise TypeError("core_tools plugin must be CoreToolsPlugin")
        ctx.config["tool_registry"] = core_tools_plugin.registry
    if "skills" in registry.plugin_ids():
        ctx.config["skills_plugin"] = registry.get("skills")
    if "mcp" in registry.plugin_ids():
        ctx.config["mcp_plugin"] = registry.get("mcp")

    return pipeline, ctx


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
    return create_child_pipeline(
        parent_provider=None,
        tape_fork=Tape(),
        tool_filter=None,
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


if __name__ == "__main__":
    main()
