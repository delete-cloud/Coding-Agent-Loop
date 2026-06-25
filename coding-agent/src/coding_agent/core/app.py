from __future__ import annotations

import os
import inspect
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentkit.config.loader import load_config
from agentkit.directive.executor import DirectiveExecutor
from agentkit.environment import Environment
from agentkit.plugin.registry import PluginRegistry
from agentkit.runtime import AgentRunContext, ContextBudget
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.runtime.hookspecs import HOOK_SPECS
from agentkit.runtime.pipeline import Pipeline, PipelineContext
from agentkit.tape.tape import Tape

from coding_agent.core.agent_identity import legacy_agent_id_str
from coding_agent.approval import ApprovalPolicy
from coding_agent.environment import LocalEnvironment
from coding_agent.environment.additional_roots import with_additional_workspace_roots
from coding_agent.plugins.approval import ApprovalPlugin
from coding_agent.plugins.core_tools import CoreToolsPlugin
from coding_agent.plugins.doom_detector import DoomDetectorPlugin
from coding_agent.plugins.llm_provider import LLMProviderPlugin
from coding_agent.plugins.mcp import MCPPlugin
from coding_agent.plugins.memory import MemoryPlugin
from coding_agent.plugins.kb import KBPlugin
from coding_agent.plugins.metrics import SessionMetricsPlugin
from coding_agent.observability import build_observation_sink
from coding_agent.plugins.parallel_executor import ParallelExecutorPlugin
from coding_agent.plugins.semantic_memory import SemanticMemoryPlugin
from coding_agent.plugins.shell_session import ShellSessionPlugin
from coding_agent.plugins.skills import SkillsPlugin
from coding_agent.plugins.storage import StoragePlugin
from coding_agent.plugins.summarizer import SummarizerPlugin
from coding_agent.subagents.coordinator import ChildWorkerCoordinator
from coding_agent.topics.memory import MemoryReviewStore
from coding_agent.topics.range_index import TopicRangeIndex
from coding_agent.topics.semantic_backends import (
    FAKE_SEMANTIC_INDEX_SCHEMA,
    SemanticIndexSchema,
)
from coding_agent.topics.semantic_backends import registry as semantic_backend_registry
from coding_agent.topics.semantic_index import SafeSemanticMemoryIndex
from coding_agent.topics.semantic_recall import SemanticTopicStore
from coding_agent.topics.semantic_sync import (
    SemanticMemoryReviewSyncService,
    SemanticMemorySyncer,
)
from coding_agent.tools.web_search import create_web_search_backend

ToolFilter = Any


@dataclass(frozen=True)
class SemanticMemoryConfig:
    enabled: bool = False
    backend: str = "fake"
    schema: SemanticIndexSchema = FAKE_SEMANTIC_INDEX_SCHEMA

    def to_config_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "backend": self.backend,
        }


@dataclass(frozen=True)
class MemorySwitchConfig:
    enabled: bool = True
    read_enabled: bool = True
    write_enabled: bool = True
    semantic: SemanticMemoryConfig = field(default_factory=SemanticMemoryConfig)

    @property
    def effective_read_enabled(self) -> bool:
        return self.enabled and self.read_enabled

    @property
    def effective_write_enabled(self) -> bool:
        return self.enabled and self.write_enabled

    def to_config_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "read_enabled": self.read_enabled,
            "write_enabled": self.write_enabled,
            "effective_read_enabled": self.effective_read_enabled,
            "effective_write_enabled": self.effective_write_enabled,
            "semantic": self.semantic.to_config_dict(),
        }


def _local_workspace_root(environment: Environment) -> Path | None:
    local_root = environment.workspace_summary().local_root
    if local_root is None:
        return None
    return Path(local_root).expanduser().resolve()


def _legacy_config_agent_id(run_context: AgentRunContext) -> str:
    return legacy_agent_id_str(run_context.agent_id)


def _run_trace_metadata(
    environment: Environment,
    trace_metadata: Mapping[str, object] | None,
) -> dict[str, object]:
    metadata = dict(trace_metadata or {})
    for key in list(metadata):
        if key.startswith("cloud."):
            del metadata[key]

    if environment.kind != "cloud":
        return metadata

    workspace_id = environment.tool_config().get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise ValueError("cloud environment must expose string workspace_id")
    metadata["cloud.workspace_id"] = workspace_id
    return metadata


def _environment_tool_config(environment: Environment) -> dict[str, Any]:
    config = environment.tool_config()
    return dict(config)


def _resolve_additional_workspace_roots(
    roots: list[str] | None,
) -> tuple[Path, ...]:
    if roots is None:
        return ()
    return tuple(Path(root).expanduser().resolve() for root in roots)


def _validate_semantic_topic_dependencies(
    *,
    semantic_topic_store: SemanticTopicStore | None,
    semantic_topic_index: TopicRangeIndex | None,
) -> None:
    if semantic_topic_store is not None:
        load_topic = getattr(semantic_topic_store, "load_topic", None)
        if not callable(load_topic) or not inspect.iscoroutinefunction(load_topic):
            raise TypeError(
                "semantic_topic_store must provide async load_topic(topic_id)"
            )
    if semantic_topic_index is not None and not isinstance(
        semantic_topic_index, TopicRangeIndex
    ):
        raise TypeError("semantic_topic_index must be TopicRangeIndex")


def _merge_shell_config(
    environment_config: Mapping[str, object],
    file_config: Mapping[str, object],
) -> dict[str, object]:
    env_shell = environment_config.get("shell", {})
    if env_shell is None:
        env_shell = {}
    if not isinstance(env_shell, Mapping):
        raise ValueError("environment shell tool config must be a mapping")
    merged = dict(env_shell)
    merged.update(file_config)
    return merged


def _parse_memory_switch_config(extra: Mapping[str, Any]) -> MemorySwitchConfig:
    raw_memory_cfg = extra.get("memory", {})
    if raw_memory_cfg is None:
        raw_memory_cfg = {}
    if not isinstance(raw_memory_cfg, Mapping):
        raise ValueError("[memory] config must be a table")
    return MemorySwitchConfig(
        enabled=_memory_bool(raw_memory_cfg, "enabled"),
        read_enabled=_memory_bool(raw_memory_cfg, "read_enabled"),
        write_enabled=_memory_bool(raw_memory_cfg, "write_enabled"),
        semantic=_parse_semantic_memory_config(raw_memory_cfg),
    )


def _memory_bool(config: Mapping[str, Any], field: str) -> bool:
    value = config.get(field, True)
    if type(value) is not bool:
        raise ValueError(f"[memory].{field} must be a boolean")
    return value


def _parse_semantic_memory_config(
    memory_config: Mapping[str, Any],
) -> SemanticMemoryConfig:
    raw_semantic_cfg = memory_config.get("semantic", {})
    if raw_semantic_cfg is None:
        raw_semantic_cfg = {}
    if not isinstance(raw_semantic_cfg, Mapping):
        raise ValueError("[memory.semantic] config must be a table")
    enabled = raw_semantic_cfg.get("enabled", False)
    if type(enabled) is not bool:
        raise ValueError("[memory.semantic].enabled must be a boolean")
    backend = raw_semantic_cfg.get("backend", "fake")
    if not isinstance(backend, str):
        raise ValueError("[memory.semantic].backend must be a string")
    if (
        enabled
        and backend
        not in semantic_backend_registry.available_semantic_memory_backends()
    ):
        raise ValueError(f"unknown semantic memory backend: {backend}")
    return SemanticMemoryConfig(
        enabled=enabled,
        backend=backend,
    )


def _child_system_prompt_suffix(tool_filter: ToolFilter) -> str:
    if tool_filter is None:
        return ""
    if _should_include_tool(tool_filter, "subagent"):
        return ""
    return (
        "\n\nYou are running as a child agent. "
        "Nested subagent delegation is unavailable in this child session, "
        "so do not claim that you can call the subagent tool and do not ask to use it."
    )


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
    environment: Environment | None = None,
    max_steps_override: int | None = None,
    approval_mode_override: str | None = None,
    session_id_override: str | None = None,
    run_id_override: str | None = None,
    agent_id_override: str | None = None,
    parent_run_id_override: str | None = None,
    context_budget: ContextBudget | None = None,
    trace_metadata: Mapping[str, Any] | None = None,
    mcp_servers_override: dict[str, dict[str, Any]] | None = None,
    additional_workspace_roots_override: list[str] | None = None,
    semantic_topic_store: SemanticTopicStore | None = None,
    semantic_topic_index: TopicRangeIndex | None = None,
) -> tuple[Any, Any]:
    if config_path is None:
        config_path = Path(__file__).parents[1] / "agent.toml"
    if data_dir is None:
        data_dir = Path(os.environ.get("AGENT_DATA_DIR", "./data"))

    if environment is None:
        environment = LocalEnvironment(workspace_root or Path.cwd())
    _validate_semantic_topic_dependencies(
        semantic_topic_store=semantic_topic_store,
        semantic_topic_index=semantic_topic_index,
    )
    resolved_additional_workspace_roots = _resolve_additional_workspace_roots(
        additional_workspace_roots_override
    )
    environment = with_additional_workspace_roots(
        environment,
        resolved_additional_workspace_roots,
    )
    local_workspace_root = _local_workspace_root(environment)
    environment_config = _environment_tool_config(environment)

    cfg = load_config(config_path)

    # Env vars override toml; explicit function args override env vars.
    env_provider = os.environ.get("AGENT_PROVIDER")
    env_model = os.environ.get("AGENT_MODEL")
    env_base_url = os.environ.get("AGENT_BASE_URL")
    if env_provider:
        cfg.provider = env_provider
    if env_model:
        cfg.model = env_model

    if model_override:
        cfg.model = model_override
    if provider_override:
        cfg.provider = provider_override
    resolved_base_url = base_url_override or env_base_url or cfg.base_url
    if max_steps_override is not None:
        cfg.max_turns = max_steps_override
    cfg.system_prompt += _child_system_prompt_suffix(tool_filter)

    resolved_key = api_key or os.environ.get("AGENT_API_KEY")
    if not resolved_key and cfg.provider == "copilot":
        resolved_key = os.environ.get("GITHUB_TOKEN", "")
    if not resolved_key and cfg.provider == "kimi":
        resolved_key = os.environ.get("MOONSHOT_API_KEY", "")
    if not resolved_key and cfg.provider in ("kimi-code", "kimi-code-anthropic"):
        resolved_key = os.environ.get("KIMI_CODE_API_KEY", "")
    if not resolved_key and cfg.provider == "deepseek":
        resolved_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not resolved_key and cfg.provider == "stepfun":
        resolved_key = os.environ.get("STEP_API_KEY", "")
    resolved_key = resolved_key or ""

    approval_cfg = cfg.extra.get("approval", {})
    subagent_cfg = cfg.extra.get("subagent", {})
    web_search_cfg = cfg.extra.get("web_search", {})
    shell_cfg = cfg.extra.get("shell", {})
    if not isinstance(shell_cfg, dict):
        raise ValueError("[shell] config must be a table")
    memory_cfg = _parse_memory_switch_config(cfg.extra)
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
    sum_cfg = cfg.extra.get("summarizer", {})
    parallel_cfg = cfg.extra.get("parallel", {})
    doom_cfg = cfg.extra.get("doom_detector", {})
    skills_cfg = cfg.extra.get("skills", {})
    mcp_cfg = cfg.extra.get("mcp", {})
    storage_cfg = cfg.extra.get("storage", {})
    kb_cfg = cfg.extra.get("kb", {})
    kb_corpus = kb_cfg.get("corpus", "default")
    if not isinstance(kb_corpus, str):
        raise ValueError("[kb].corpus must be a string")
    kb_search_corpora = kb_cfg.get("search_corpora")
    if kb_search_corpora is not None and not isinstance(kb_search_corpora, list):
        raise ValueError("[kb].search_corpora must be a list")
    if kb_search_corpora is not None and not all(
        isinstance(item, str) for item in kb_search_corpora
    ):
        raise ValueError("[kb].search_corpora must contain only strings")
    if "min_score" in kb_cfg:
        raise ValueError(
            "[kb].min_score is not supported; KB score is LanceDB distance, "
            "so use [kb].max_distance instead"
        )
    kb_max_distance = kb_cfg.get("max_distance")
    if kb_max_distance is not None:
        if not isinstance(kb_max_distance, int | float) or isinstance(
            kb_max_distance, bool
        ):
            raise ValueError("[kb].max_distance must be a number")
        kb_max_distance = float(kb_max_distance)
        if kb_max_distance < 0:
            raise ValueError("[kb].max_distance must be non-negative")
    kb_db_path = kb_cfg.get("db_path", "kb")
    if not isinstance(kb_db_path, str):
        raise ValueError("[kb].db_path must be a string")
    memory_review_store = MemoryReviewStore(
        data_dir / kb_db_path / "reviewed_memory.jsonl",
        candidate_writes_enabled=memory_cfg.effective_write_enabled,
    )
    semantic_memory_backend = None
    semantic_memory_index = None
    semantic_memory_syncer = None
    semantic_memory_review_sync_service = None
    if memory_cfg.semantic.enabled:
        semantic_memory_backend = (
            semantic_backend_registry.create_semantic_memory_backend(
                memory_cfg.semantic.backend, schema=memory_cfg.semantic.schema
            )
        )
        semantic_memory_index = SafeSemanticMemoryIndex(semantic_memory_backend)
        semantic_memory_syncer = SemanticMemorySyncer(
            index=semantic_memory_index,
            backend=semantic_memory_backend,
            schema=memory_cfg.semantic.schema,
        )
        semantic_memory_review_sync_service = SemanticMemoryReviewSyncService(
            review_store=memory_review_store,
            syncer=semantic_memory_syncer,
        )
    observability_cfg = cfg.extra.get("observability", {})
    if not isinstance(observability_cfg, dict):
        raise ValueError("[observability] config must be a table")
    observation_sink = build_observation_sink(observability_cfg)

    def _create_child_with_environment(**kwargs: Any) -> tuple[Any, PipelineContext]:
        kwargs.setdefault("environment", environment)
        kwargs.setdefault("mcp_servers_override", mcp_servers_override)
        kwargs.setdefault(
            "additional_workspace_roots_override",
            [str(root) for root in resolved_additional_workspace_roots],
        )
        kwargs.setdefault("semantic_topic_store", semantic_topic_store)
        kwargs.setdefault("semantic_topic_index", semantic_topic_index)
        return create_child_pipeline(**kwargs)

    plugin_factories: dict[str, Any] = {
        "llm_provider": lambda: _build_llm_provider_plugin(
            provider=cfg.provider,
            model=cfg.model,
            api_key=resolved_key,
            base_url=resolved_base_url,
            parent_provider=parent_provider,
        ),
        "storage": lambda: StoragePlugin(data_dir=data_dir, config=storage_cfg),
        "core_tools": lambda: CoreToolsPlugin(
            environment=environment,
            shell_session=shell_session,
            web_search_backend=web_search_backend,
            child_pipeline_builder=_create_child_with_environment,
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
        "memory": lambda: MemoryPlugin(
            read_enabled=memory_cfg.effective_read_enabled,
            write_enabled=memory_cfg.effective_write_enabled,
        ),
        "shell_session": lambda: shell_session,
    }
    if memory_cfg.semantic.enabled and memory_cfg.effective_read_enabled:
        if semantic_memory_index is None:
            raise TypeError("semantic memory index must be initialized when enabled")
        plugin_factories["semantic_memory"] = lambda: SemanticMemoryPlugin(
            semantic_index=semantic_memory_index,
            memory_review_store=memory_review_store,
            read_enabled=memory_cfg.effective_read_enabled,
            topic_store=semantic_topic_store,
            topic_index=semantic_topic_index,
        )

    async def _execute_tool_async(
        name: str,
        arguments: dict[str, Any],
        *,
        ctx: PipelineContext | None = None,
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
            return await execute_tool_async(
                name=name,
                arguments=arguments,
                ctx=ctx,
            )
        return await execute_tool_async(name=name, arguments=arguments)

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
            "skills": lambda: SkillsPlugin(
                workspace_root=local_workspace_root,
                extra_dirs=skills_cfg.get("extra_dirs", []),
            ),
            "mcp": lambda: MCPPlugin(
                servers=(
                    mcp_servers_override
                    if mcp_servers_override is not None
                    else mcp_cfg.get("servers", {})
                ),
            ),
            "kb": lambda: KBPlugin(
                db_path=data_dir / kb_db_path,
                embedding_model=kb_cfg.get("embedding_model", "text-embedding-3-small"),
                embedding_base_url=kb_cfg.get("embedding_base_url"),
                embedding_dim=int(kb_cfg.get("embedding_dim", 1536)),
                chunk_size=int(kb_cfg.get("chunk_size", 1200)),
                chunk_overlap=int(kb_cfg.get("chunk_overlap", 200)),
                top_k=int(kb_cfg.get("top_k", 5)),
                max_distance=kb_max_distance,
                index_extensions=kb_cfg.get(
                    "index_extensions",
                    [".md", ".txt", ".rst", ".yaml", ".yml", ".toml"],
                ),
                corpus=kb_corpus,
                search_corpora=kb_search_corpora,
            ),
        }
    )

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
        _mem = registry.get("memory")
        if isinstance(_mem, MemoryPlugin):
            memory_plugin = _mem

    async def _memory_handler(directive: Any) -> None:
        if memory_plugin is not None:
            memory_plugin.add_memory(directive)

    directive_executor = DirectiveExecutor(
        memory_handler=(
            _memory_handler
            if memory_plugin is not None and memory_cfg.effective_write_enabled
            else None
        ),
    )

    pipeline = Pipeline(
        runtime=runtime,
        registry=registry,
        directive_executor=directive_executor,
    )

    if session_id_override is None:
        session_id = uuid.uuid4().hex
    else:
        if not session_id_override:
            raise ValueError("session_id_override must be None or a non-empty string")
        session_id = session_id_override
    if run_id_override is None:
        run_id = uuid.uuid4().hex
    else:
        if not run_id_override:
            raise ValueError("run_id_override must be None or a non-empty string")
        run_id = run_id_override
    run_context = AgentRunContext(
        session_id=session_id,
        run_id=run_id,
        agent_id=agent_id_override,
        parent_run_id=parent_run_id_override,
        environment=environment,
        context_budget=ContextBudget() if context_budget is None else context_budget,
        trace_metadata=_run_trace_metadata(environment, trace_metadata),
    )

    ctx_config: dict[str, Any] = {
        "system_prompt": cfg.system_prompt,
        "model": cfg.model,
        "provider": cfg.provider,
        "approval_mode": policy_str,
        "max_tool_rounds": cfg.max_turns,
        "agent_id": _legacy_config_agent_id(run_context),
        "subagent_timeout": float(subagent_cfg.get("timeout", 30.0)),
        "child_worker_coordinator": ChildWorkerCoordinator(),
        "web_search": web_search_cfg,
        "environment": environment,
        "shell": _merge_shell_config(environment_config, shell_cfg),
        "structured_tool_result_scope": structured_tool_result_scope,
        "memory_review_store": memory_review_store,
        "memory": memory_cfg.to_config_dict(),
        "topic_recall": {"enabled": memory_cfg.effective_read_enabled},
    }
    if "isolation_policy" in environment_config:
        ctx_config["isolation_policy"] = environment_config["isolation_policy"]
    if resolved_additional_workspace_roots:
        ctx_config["additional_workspace_roots"] = [
            str(root) for root in resolved_additional_workspace_roots
        ]
    if observation_sink is not None:
        ctx_config["observation_sink"] = observation_sink
    if local_workspace_root is not None:
        ctx_config["workspace_root"] = str(local_workspace_root)
    if semantic_memory_backend is not None:
        ctx_config["semantic_memory_backend"] = semantic_memory_backend
    if semantic_memory_index is not None:
        ctx_config["semantic_memory_index"] = semantic_memory_index
    if semantic_memory_syncer is not None:
        ctx_config["semantic_memory_syncer"] = semantic_memory_syncer
    if semantic_memory_review_sync_service is not None:
        ctx_config["semantic_memory_review_sync_service"] = (
            semantic_memory_review_sync_service
        )
    if semantic_topic_store is not None:
        ctx_config["semantic_topic_store"] = semantic_topic_store
    if semantic_topic_index is not None:
        ctx_config["semantic_topic_index"] = semantic_topic_index

    ctx = PipelineContext(
        tape=tape_fork,
        session_id=session_id,
        run_context=run_context,
        config=ctx_config,
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
    environment: Environment | None = None,
    max_steps_override: int | None = None,
    approval_mode_override: str | None = None,
    session_id_override: str | None = None,
    run_id_override: str | None = None,
    agent_id_override: str | None = None,
    parent_run_id_override: str | None = None,
    context_budget: ContextBudget | None = None,
    trace_metadata: Mapping[str, Any] | None = None,
    mcp_servers_override: dict[str, dict[str, Any]] | None = None,
    additional_workspace_roots_override: list[str] | None = None,
    tape: Tape | None = None,
    semantic_topic_store: SemanticTopicStore | None = None,
    semantic_topic_index: TopicRangeIndex | None = None,
) -> tuple[Any, Any]:
    return create_child_pipeline(
        parent_provider=None,
        tape_fork=tape or Tape(),
        tool_filter=None,
        config_path=config_path,
        data_dir=data_dir,
        api_key=api_key,
        model_override=model_override,
        provider_override=provider_override,
        base_url_override=base_url_override,
        workspace_root=workspace_root,
        environment=environment,
        max_steps_override=max_steps_override,
        approval_mode_override=approval_mode_override,
        session_id_override=session_id_override,
        run_id_override=run_id_override,
        agent_id_override=agent_id_override,
        parent_run_id_override=parent_run_id_override,
        context_budget=context_budget,
        trace_metadata=trace_metadata,
        mcp_servers_override=mcp_servers_override,
        additional_workspace_roots_override=additional_workspace_roots_override,
        semantic_topic_store=semantic_topic_store,
        semantic_topic_index=semantic_topic_index,
    )
