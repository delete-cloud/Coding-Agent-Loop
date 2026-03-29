"""
存储后端声明式接入设计

目标：不写死任何存储实现，通过配置文件声明使用哪个后端。
方式：Registry 模式 + 配置驱动 Factory。
不依赖：pluggy、entry_points、importlib.metadata。

用法：
  1. 实现 Protocol 接口
  2. 用 @register 装饰器注册
  3. 在 agent.toml 中声明
  4. 启动时 factory 自动解析
"""

# ============================================================
# 第一层：Protocol 接口（不变）
# ============================================================

from __future__ import annotations

from typing import Protocol, Any, runtime_checkable
from dataclasses import dataclass
from enum import Enum


@runtime_checkable
class TapeStore(Protocol):
    async def append(self, session_id: str, entry: "Entry") -> None: ...
    async def entries(self, session_id: str, since_anchor: str | None = None) -> list["Entry"]: ...
    async def anchors(self, session_id: str) -> list["Entry"]: ...
    async def fork(self, session_id: str, fork_id: str) -> str: ...
    async def merge(self, fork_id: str, target_session_id: str) -> None: ...


@runtime_checkable
class DocIndex(Protocol):
    async def search(self, query: str, top_k: int = 5) -> list[Any]: ...
    async def upsert(self, doc_id: str, content: str, source: str) -> None: ...


@runtime_checkable
class SessionStore(Protocol):
    async def get(self, session_id: str) -> dict | None: ...
    async def save(self, session_id: str, metadata: dict) -> None: ...


# ============================================================
# 第二层：Registry（全局注册表）
# ============================================================

class BackendRegistry:
    """
    全局后端注册表。

    每个 Protocol 对应一个 namespace（"tape"、"doc_index"、"session"），
    每个 namespace 下可以注册多个命名实现。
    """

    _registry: dict[str, dict[str, type]] = {}

    @classmethod
    def register(cls, namespace: str, name: str):
        """装饰器：注册一个后端实现。

        用法：
            @BackendRegistry.register("tape", "postgres")
            class PostgresTapeStore:
                ...
        """
        def decorator(impl_class: type) -> type:
            if namespace not in cls._registry:
                cls._registry[namespace] = {}
            cls._registry[namespace][name] = impl_class
            return impl_class
        return decorator

    @classmethod
    def get(cls, namespace: str, name: str) -> type:
        """按名字查找已注册的实现类。"""
        if namespace not in cls._registry:
            raise KeyError(f"Unknown namespace: {namespace!r}. Available: {list(cls._registry.keys())}")
        impls = cls._registry[namespace]
        if name not in impls:
            raise KeyError(f"Unknown backend {name!r} for {namespace!r}. Available: {list(impls.keys())}")
        return impls[name]

    @classmethod
    def available(cls, namespace: str) -> list[str]:
        """列出某个 namespace 下所有已注册的后端名。"""
        return list(cls._registry.get(namespace, {}).keys())


# 简写别名
register = BackendRegistry.register


# ============================================================
# 第三层：内置实现（每个用 @register 注册）
# ============================================================

# ---------- Tape ----------

@register("tape", "jsonl")
class JSONLTapeStore:
    """零依赖本地实现，开发/测试用。"""

    def __init__(self, *, data_dir: str = "./data/tapes", **_kwargs):
        self.data_dir = data_dir

    async def append(self, session_id, entry):
        ...  # 写入 {data_dir}/{session_id}.jsonl

    async def entries(self, session_id, since_anchor=None):
        ...

    async def anchors(self, session_id):
        ...

    async def fork(self, session_id, fork_id):
        ...

    async def merge(self, fork_id, target_session_id):
        ...


@register("tape", "postgres")
class PostgresTapeStore:
    """生产实现，需要 asyncpg。"""

    def __init__(self, *, dsn: str, pool_min: int = 2, pool_max: int = 10, **_kwargs):
        self.dsn = dsn
        self.pool_min = pool_min
        self.pool_max = pool_max
        self._pool = None

    async def _ensure_pool(self):
        if self._pool is None:
            import asyncpg
            self._pool = await asyncpg.create_pool(
                self.dsn, min_size=self.pool_min, max_size=self.pool_max
            )

    async def append(self, session_id, entry):
        await self._ensure_pool()
        ...

    async def entries(self, session_id, since_anchor=None):
        await self._ensure_pool()
        ...

    async def anchors(self, session_id):
        await self._ensure_pool()
        ...

    async def fork(self, session_id, fork_id):
        ...

    async def merge(self, fork_id, target_session_id):
        ...


# ---------- DocIndex ----------

@register("doc_index", "pgvector")
class PgVectorDocIndex:
    """PostgreSQL + pgvector 实现。"""

    def __init__(self, *, dsn: str, embedding_model: str = "text-embedding-3-small", **_kwargs):
        self.dsn = dsn
        self.embedding_model = embedding_model

    async def search(self, query, top_k=5):
        ...

    async def upsert(self, doc_id, content, source):
        ...


@register("doc_index", "null")
class NullDocIndex:
    """空实现，不做向量检索（纯 Tree-sitter 模式）。"""

    def __init__(self, **_kwargs):
        pass

    async def search(self, query, top_k=5):
        return []

    async def upsert(self, doc_id, content, source):
        pass


# ---------- Session ----------

@register("session", "postgres")
class PostgresSessionStore:
    def __init__(self, *, dsn: str, **_kwargs):
        self.dsn = dsn

    async def get(self, session_id):
        ...

    async def save(self, session_id, metadata):
        ...


@register("session", "memory")
class InMemorySessionStore:
    """纯内存，测试用。"""

    def __init__(self, **_kwargs):
        self._data: dict[str, dict] = {}

    async def get(self, session_id):
        return self._data.get(session_id)

    async def save(self, session_id, metadata):
        self._data[session_id] = metadata


# ============================================================
# 第四层：配置驱动的 Factory
# ============================================================

def load_config(path: str = "agent.toml") -> dict:
    """加载 TOML 配置文件。"""
    import tomllib
    with open(path, "rb") as f:
        return tomllib.load(f)


def create_backend(namespace: str, config: dict) -> Any:
    """
    从配置创建一个后端实例。

    config 格式：
        {"backend": "postgres", "dsn": "postgresql://...", ...}

    "backend" 字段指定注册名，其余字段作为 kwargs 传给构造函数。
    """
    backend_name = config.pop("backend")
    impl_class = BackendRegistry.get(namespace, backend_name)
    return impl_class(**config)


def create_all_backends(config: dict) -> dict[str, Any]:
    """
    从完整配置创建所有后端。

    config 结构（对应 agent.toml 的 [storage] 部分）：
        {
            "tape":      {"backend": "postgres", "dsn": "..."},
            "doc_index": {"backend": "pgvector", "dsn": "..."},
            "session":   {"backend": "postgres", "dsn": "..."},
        }
    """
    backends = {}
    for namespace, backend_config in config.items():
        # 深拷贝避免修改原 config
        cfg = dict(backend_config)
        backends[namespace] = create_backend(namespace, cfg)
    return backends


# ============================================================
# 第五层：AgentLoop 集成
# ============================================================

class AgentLoop:
    """
    通过 factory 注入所有存储后端。
    AgentLoop 本身不知道具体用了什么存储。
    """

    def __init__(
        self,
        tape_store: TapeStore,
        doc_index: DocIndex,
        session_store: SessionStore,
        # ... 其他依赖
    ):
        self.tape = tape_store
        self.doc_index = doc_index
        self.session = session_store


def create_agent_from_config(config_path: str = "agent.toml") -> AgentLoop:
    """一行启动：从配置文件创建完整的 AgentLoop。"""
    config = load_config(config_path)
    backends = create_all_backends(config["storage"])

    return AgentLoop(
        tape_store=backends["tape"],
        doc_index=backends["doc_index"],
        session_store=backends["session"],
    )


# ============================================================
# 第六层：用户自定义后端（第三方扩展）
# ============================================================

# 用户只需要两步：
#
# 1. 写一个类，实现 Protocol 接口
# 2. 用 @register 装饰器注册
#
# 示例：用户想接入 Redis 做 Session 存储

@register("session", "redis")
class RedisSessionStore:
    """用户自定义的 Redis session 后端。"""

    def __init__(self, *, url: str = "redis://localhost:6379", prefix: str = "session:", **_kwargs):
        self.url = url
        self.prefix = prefix

    async def get(self, session_id):
        import redis.asyncio as redis
        r = redis.from_url(self.url)
        data = await r.get(f"{self.prefix}{session_id}")
        if data:
            import json
            return json.loads(data)
        return None

    async def save(self, session_id, metadata):
        import redis.asyncio as redis
        import json
        r = redis.from_url(self.url)
        await r.set(f"{self.prefix}{session_id}", json.dumps(metadata))


# 注册后，用户在 agent.toml 中声明即可使用：
#
# [storage.session]
# backend = "redis"
# url = "redis://localhost:6379"
# prefix = "agent:session:"
#
# 不需要改 AgentLoop 的任何代码。


# ============================================================
# 第七层：外部文件扩展（可选，不需要改主项目代码）
# ============================================================

# 如果用户的自定义后端在独立的 Python 包中，
# 只需要在 agent.toml 中指定额外的 import 路径：
#
# [storage]
# plugins = ["my_company.agent_backends"]  # 启动时自动 import
#
# factory 在创建后端前先 import 这些模块，
# 模块内的 @register 装饰器会自动完成注册。

def load_plugins(plugin_modules: list[str]) -> None:
    """Import 额外的模块，触发其中的 @register 装饰器。"""
    import importlib
    for module_path in plugin_modules:
        importlib.import_module(module_path)


def create_agent_from_config_v2(config_path: str = "agent.toml") -> AgentLoop:
    """支持外部插件的完整启动流程。"""
    config = load_config(config_path)

    # 先加载外部插件（如果有）
    if "plugins" in config.get("storage", {}):
        load_plugins(config["storage"].pop("plugins"))

    backends = create_all_backends(config["storage"])

    return AgentLoop(
        tape_store=backends["tape"],
        doc_index=backends["doc_index"],
        session_store=backends["session"],
    )
