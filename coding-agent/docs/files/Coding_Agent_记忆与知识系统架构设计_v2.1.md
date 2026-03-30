# Coding Agent 记忆与知识系统架构设计 v2.1

> 更新日期：2026-03-28
> 状态：可落地方案（仅含当前可实施的变更）
> v2.1 变更：存储层统一为 PostgreSQL + pgvector，移除 LanceDB/SQLite 双引擎架构

---

## 一、当前项目侧重分析

项目 8,778 行代码、512 测试、15+ 核心模块，已完成 P0-P3。回顾已实现的内容，**投入集中在三个方向，但缺失了两个关键方向**：

### 已侧重

**1. 执行层基础设施（占比最大）**

Wire 协议、HTTP/SSE 流式、TUI 终端、会话管理、输入验证、认证、限流——P2 整个阶段都在做 agent 与外界的通信管道。这部分代码量 2,500+ 行，质量高（已 hardened），但本质上是**传输层**，不直接影响 agent 的智能水平。

**2. 安全与可控性**

Approval 系统、Pydantic 验证、API Key 认证、CORS、速率限制、Doom loop 检测。这些是生产化的必要条件，但同样属于**围栏**而非**引擎**。

**3. 上下文管理（初版）**

KB（LanceDB 向量搜索）、Context 智能压缩、Summarizer、Skills 懒加载、Session 持久化（SQLite）。这部分是距离"记忆系统"最近的实现，但目前是**扁平的**——所有知识都平等对待，缺少分层、缺少生命周期管理、缺少遗忘。此外，存储层存在 LanceDB + SQLite 双引擎的碎片化问题，不利于 K8s 部署和统一运维。

### 未侧重

**4. 状态管理与知识生命周期（缺失）**

没有 Tape 层——agent 的操作历史是隐式的（散落在 session 和 context 中），不是显式的、结构化的、可查询的事实链。没有知识类型区分（fact vs decision vs plan），没有知识演化追踪，没有遗忘机制。

**5. 框架通用性（未抽象）**

所有代码直接面向 coding 场景，核心框架与 coding-specific 逻辑耦合在同一层目录中。Wire、Loop、Context、Session 本身是 agent-agnostic 的，但没有被显式分离。

### 一句话总结

> **当前项目做了一个扎实的"能跑起来的 coding agent"，但在"agent 如何积累和运用知识"这件事上还停留在最基础的阶段。**

---

## 二、记忆与知识系统的核心设计原则

基于 Bub（Tape/Hook-first）、Kapy（Grounding/Getter 双模式）、Nowledge（Trace→Unit→Crystal 知识形态）的研究，提炼出四条设计原则：

**原则 1：状态，不是记忆。**
不模拟人类记忆的模糊语义，而是做确定性的状态管理。Tape 是事实的单一来源（source of truth），所有派生数据（摘要、索引、检索结果）都可以从 Tape 重建。

**原则 2：构建，不是继承。**
上下文窗口不是累积的聊天历史，而是每次 LLM 调用前按需组装的工作集。从最近相关的 anchor 开始重建，而非从 session 开头线性截取。

**原则 3：分层存储，按需检索。**
不同生命周期的知识放在不同的层——固定注入的不需要检索，操作日志按时序回溯，持久知识按语义查询。不要用一种机制处理所有类型的信息。

**原则 4：声明式可替换，但不用插件框架。**
Bub 的 pluggy Hook-first 适合 ~200 行极小内核的场景。你的项目已有 14,082 行 Python 代码和 108 个文件，引入 pluggy 意味着重写大量调用链。**用 Registry 模式 + TOML 配置替代**：每个存储实现用 `@register` 装饰器自注册，配置文件声明使用哪个后端，factory 按名字查找并实例化。效果和 Bub 一样——切换后端不改代码，只改配置——但零框架依赖。

---

## 三、三层记忆架构

```
┌──────────────────────────────────────────────────────────────┐
│                Layer 0: Grounding（固定注入）                    │
│                                                              │
│  AGENTS.md（项目规范 + 技术栈 + 架构决策）                        │
│  当前活跃 Skill 摘要                                            │
│  → 每次 LLM 调用无条件注入到 system prompt                       │
│  → 硬限制：≤200 行（超长会降低模型遵从度）                          │
├──────────────────────────────────────────────────────────────┤
│                Layer 1: Tape（事实记录）                         │
│                                                              │
│  ┌─────────┐  ┌──────────┐  ┌─────────┐  ┌───────────┐      │
│  │ Message  │→│ ToolCall  │→│ ToolResult│→│  Anchor   │      │
│  │ (用户输入)│  │(bash/edit)│  │ (执行结果) │  │(阶段转换) │      │
│  └─────────┘  └──────────┘  └─────────┘  └───────────┘      │
│                                                              │
│  特性：append-only | 结构化 anchor | fork/merge                │
│  职责：当前任务的操作日志、阶段状态、因果链                          │
│  存储：PostgreSQL tape_entries 表（JSONB payload）              │
├──────────────────────────────────────────────────────────────┤
│                Layer 2: Knowledge Store（持久知识）               │
│                                                              │
│  ┌────────────────────┐  ┌──────────────────────┐            │
│  │ 代码索引             │  │ 文档索引               │            │
│  │ Tree-sitter 符号图   │  │ pgvector 语义检索      │            │
│  │ （主路径，无 embedding）│  │（Skill 文档、API 文档）  │            │
│  └────────────────────┘  └──────────────────────┘            │
│                                                              │
│  职责：跨 session 持久知识，agent 通过 tool call 按需检索          │
│  存储：PostgreSQL + pgvector（向量列 + HNSW 索引）               │
└──────────────────────────────────────────────────────────────┘

底层统一存储：PostgreSQL 16 + pgvector 扩展
├── 本地开发：docker run postgres:16 （单实例）
├── K8s 部署：CloudNativePG Operator（3 副本 + 自动备份）
└── 替代 SQLite（会话）+ LanceDB（向量）双引擎架构
```

### 三层的职责边界

| 维度 | Layer 0 Grounding | Layer 1 Tape | Layer 2 Knowledge Store |
|------|-------------------|-------------|------------------------|
| 回答的问题 | "我是谁，在什么项目里" | "刚才做了什么，为什么" | "关于 X 我们知道什么" |
| 注入方式 | 每次无条件注入 | 从 anchor 重建，写入 prompt | Agent tool call 按需检索 |
| 数据生命周期 | 手动维护，很少变 | 当前任务/会话 | 跨会话持久 |
| 更新频率 | 低（项目级别） | 高（每个 action） | 中（索引/提炼） |
| 典型内容 | 技术栈、编码规范、架构约定 | 操作日志、编辑历史、测试结果 | API 文档、代码结构、历史经验 |
| 存储 | Markdown 文件 | PostgreSQL `tape_entries` 表 | PostgreSQL pgvector + 文件系统 |

---

## 四、Tape 详细设计

### 4.1 Entry 结构

```python
@dataclass(frozen=True)
class Entry:
    id: str                    # ULID（时间排序 + 唯一）
    kind: EntryKind            # 操作类型
    payload: dict              # 内容
    meta: dict                 # 扩展元数据

class EntryKind(str, Enum):
    MESSAGE = "message"        # 用户输入 / agent 回复
    TOOL_CALL = "tool_call"    # 工具调用
    TOOL_RESULT = "tool_result"# 工具结果
    ANCHOR = "anchor"          # 阶段转换锚点
    PLAN = "plan"              # 规划输出
```

### 4.2 Anchor 结构（核心创新点）

Anchor 不只是"标记"，而是携带最小继承状态的阶段分界线：

```python
@dataclass
class AnchorPayload:
    phase: str                 # 阶段名（如 "analyzing", "implementing", "testing"）
    summary: str               # 本阶段摘要（≤500 tokens）
    decisions: list[str]       # 本阶段做出的关键决策
    next_steps: list[str]      # 传递给下一阶段的待办
    knowledge_type: str        # fact | decision | plan | procedure
    evolves: dict | None       # 与前序 anchor 的关系
    # evolves 示例：{"ref": "anchor_id_xxx", "relation": "replaces"}
    # relation 取值：replaces | enriches | confirms | challenges
```

### 4.3 Tape 核心接口

```python
from typing import Protocol

class TapeStore(Protocol):
    """存储后端协议——不用 pluggy，用 Protocol 实现可替换"""

    async def append(self, session_id: str, entry: Entry) -> None: ...
    async def entries(self, session_id: str, since_anchor: str | None = None) -> list[Entry]: ...
    async def anchors(self, session_id: str) -> list[Entry]: ...
    async def fork(self, session_id: str, fork_id: str) -> str: ...
    async def merge(self, fork_id: str, target_session_id: str) -> None: ...

class JSONLTapeStore:
    """本地开发用：JSONL 文件，零依赖快速启动"""
    # 每个 session 一个 .jsonl 文件
    # append-only，永不修改已有行
    # 适用场景：单机开发、CI 测试

class PostgresTapeStore:
    """生产实现：PostgreSQL（本地 Docker 或 K8s CloudNativePG）"""
    # tape_entries 表，JSONB 存 payload
    # 支持跨 session 搜索 anchor、时序索引
    # 同一个 PostgreSQL 实例同时服务 Tape + Session + 向量检索
```

### 4.4 上下文重建流程

每次 LLM 调用前，上下文按以下顺序组装：

```
1. 加载 Grounding（AGENTS.md + 活跃 Skill 摘要）    → 固定 ~500 tokens
2. 找到最近 anchor                                  → O(1)
3. 从 anchor 取 summary + decisions + next_steps     → ~200 tokens
4. 加载 anchor 之后的所有 entries                     → 动态
5. 如果超出 token 预算，压缩旧 entries（调用 summarizer）
6. 组装为 messages 列表发给 LLM
```

与当前架构的差异：**现在是从 session 开头线性截取再压缩，改为从 anchor 开始精确重建。** 现有的 `core/context.py` 和 `summarizer/` 不需要重写，只需要让它们感知 anchor 的存在。

### 4.5 Triage Gate（过滤门控）

不是每条 entry 都值得进入摘要/提炼管线：

```python
class TriageGate:
    """轻量预过滤（~50 tokens LLM 调用，或纯规则）"""

    SKIP_PATTERNS = [
        # 纯规则过滤（零成本）
        lambda e: e.kind == "tool_result" and len(e.payload.get("output", "")) > 5000,
        # 超长工具输出（如 ls -la 的完整结果）→ 只保留不摘要
        lambda e: e.kind == "tool_call" and e.payload.get("name") in ("ls", "cat", "pwd"),
        # 信息查询类工具调用 → 不需要摘要
    ]

    ALWAYS_PROCESS = [
        lambda e: e.kind == "anchor",
        lambda e: e.kind == "message",
        lambda e: e.kind == "tool_result" and e.payload.get("has_error"),
        # 错误结果一定要摘要，包含 debug 价值
    ]
```

---

## 五、Knowledge Store 详细设计

### 5.1 代码索引：Tree-sitter 符号图（替代纯 embedding）

当前 `kb.py` 用 LanceDB 做向量搜索。对于代码检索，引入 Tree-sitter 作为主路径：

```python
class CodeIndex(Protocol):
    """代码索引协议"""
    async def build(self, root_path: str) -> None: ...
    async def query(self, intent: str, token_budget: int) -> list[CodeSnippet]: ...
    async def get_symbol(self, name: str) -> SymbolInfo | None: ...

class TreeSitterIndex:
    """Tree-sitter + PageRank 实现（参考 Aider repo-map）"""

    async def build(self, root_path: str) -> None:
        # 1. tree-sitter 解析所有 .py 文件
        # 2. 提取：函数定义、类定义、import 关系
        # 3. 构建有向图：节点=符号，边=引用关系
        # 4. PageRank 计算每个符号的重要性
        pass

    async def query(self, intent: str, token_budget: int) -> list[CodeSnippet]:
        # 1. 从 intent 提取关键词/符号名
        # 2. 从图中找到相关符号
        # 3. 按 PageRank 排序
        # 4. 二分搜索：在 token_budget 内尽可能多地包含符号定义
        pass
```

### 5.2 文档索引：pgvector 替代 LanceDB

原 `kb.py` 使用 LanceDB 嵌入式向量数据库。改为 PostgreSQL + pgvector，职责不变——检索自然语言文档：

| 索引对象 | 检索方式 | 存储 |
|---------|---------|------|
| Python 源代码 | Tree-sitter 符号图 + PageRank | 内存（运行时构建） |
| Skill 文档（Markdown） | 语义 embedding + pgvector | PostgreSQL `doc_embeddings` 表 |
| API 文档 / README | 语义 embedding + pgvector | PostgreSQL `doc_embeddings` 表 |
| Tape anchor 的摘要 | 语义 embedding + pgvector | PostgreSQL `doc_embeddings` 表 |

```python
class PgVectorDocIndex:
    """pgvector 实现，替代 LanceDB"""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def search(self, query: str, top_k: int = 5) -> list[DocResult]:
        embedding = await self.embed(query)
        rows = await self.pool.fetch("""
            SELECT id, content, source, 1 - (embedding <=> $1) AS similarity
            FROM doc_embeddings
            ORDER BY embedding <=> $1
            LIMIT $2
        """, embedding, top_k)
        return [DocResult(**row) for row in rows]

    async def upsert(self, doc_id: str, content: str, source: str) -> None:
        embedding = await self.embed(content)
        await self.pool.execute("""
            INSERT INTO doc_embeddings (id, content, source, embedding)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (id) DO UPDATE SET content=$2, embedding=$4
        """, doc_id, content, source, embedding)
```

**为什么从 LanceDB 换到 pgvector：**

| 维度 | LanceDB | pgvector | 选择理由 |
|------|---------|----------|---------|
| 架构 | 嵌入式（进程内） | 独立服务（C/S） | pgvector 可独立扩展，适合 K8s |
| K8s 部署 | 无官方 Helm/Operator | CloudNativePG Operator（CNCF 项目） | pgvector 的 K8s 生态完善 |
| 存储统一性 | 需额外引擎管 Session/Tape | 一个 PostgreSQL 覆盖全部 | 减少运维组件数 |
| 事务性 | 无 ACID | 完整 ACID | Tape append + 向量写入可在同一事务 |
| 混合查询 | 需外部拼接 | SQL WHERE + 向量相似度一条语句 | 元数据过滤天然支持 |
| 规模适用性 | 大于内存的数据集 | ≤500 万向量表现良好 | 项目规模远小于此上限 |
| 学习价值 | 低（嵌入式无运维可学） | 高（PostgreSQL 生态、Operator 模式） | 显著 |

### 5.3 从 Tape 到 Knowledge Store 的提炼

```
Tape entries  ──triage gate──→  有价值的 entries
                                    │
                            ┌───────┴───────┐
                            ▼               ▼
                    anchor 摘要        错误模式/解决方案
                    (即时写入)        (异步后台提炼)
                            │               │
                            ▼               ▼
                    PostgreSQL          PostgreSQL
                    doc_embeddings      doc_embeddings
                    (anchor 语义索引)   (历史经验索引)
```

**不做 Crystal（结晶）**。Nowledge 的 Trace→Unit→Crystal 管线对于跨工具个人知识管理有价值，但对于单项目 coding agent 来说过重。当前阶段只做 Tape entries → anchor 摘要 → 可选写入 pgvector 的简单提炼。

---

## 五-B、统一存储层：PostgreSQL + pgvector

### 为什么统一到一个数据库

当前项目使用 SQLite（会话持久化）+ LanceDB（向量检索）双引擎。在引入 Tape 后会变成三引擎（+ JSONL）。统一到 PostgreSQL 的收益：

- **一个连接池覆盖所有存储需求**：Tape、Session、向量检索共用同一个 asyncpg Pool
- **事务一致性**：Tape entry 写入和向量索引更新可以在同一个事务中完成
- **K8s 部署只需管一个 StatefulSet**：CloudNativePG Operator 处理高可用、备份、故障恢复
- **从开发到生产路径连贯**：本地 `docker run postgres:16`，K8s 上声明式 YAML，不换技术栈

### Schema 设计

```sql
-- 启用 pgvector 扩展
CREATE EXTENSION IF NOT EXISTS vector;

-- =============================================
-- Tape: 操作日志（append-only）
-- =============================================
CREATE TABLE tape_entries (
    id          TEXT PRIMARY KEY,          -- ULID（时间排序 + 唯一）
    session_id  TEXT NOT NULL,
    kind        TEXT NOT NULL,             -- message | tool_call | tool_result | anchor | plan
    payload     JSONB NOT NULL,            -- 内容（AnchorPayload 等结构化数据）
    meta        JSONB DEFAULT '{}',        -- 扩展元数据（token_count, latency 等）
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    -- fork 支持：fork 出的 entries 带 fork_id
    fork_id     TEXT                       -- NULL = 主线，非 NULL = 分支标识
);

CREATE INDEX idx_tape_session     ON tape_entries (session_id, created_at);
CREATE INDEX idx_tape_anchors     ON tape_entries (session_id, kind) WHERE kind = 'anchor';
CREATE INDEX idx_tape_fork        ON tape_entries (fork_id) WHERE fork_id IS NOT NULL;

-- =============================================
-- Session: 会话持久化（替代 SQLite）
-- =============================================
CREATE TABLE sessions (
    id          TEXT PRIMARY KEY,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    metadata    JSONB DEFAULT '{}'          -- 会话级配置、用户偏好等
);

-- =============================================
-- Knowledge Store: 文档向量索引（替代 LanceDB）
-- =============================================
CREATE TABLE doc_embeddings (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    source      TEXT NOT NULL,              -- skill | api_doc | anchor_summary | experience
    embedding   vector(1536),               -- 维度取决于 embedding 模型
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- HNSW 索引：余弦距离，适合语义检索
CREATE INDEX idx_doc_embedding ON doc_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- 按 source 类型过滤的部分索引（加速特定类型检索）
CREATE INDEX idx_doc_source ON doc_embeddings (source);
```

### 连接管理

```python
import asyncpg

class DatabasePool:
    """统一的 PostgreSQL 连接池"""

    def __init__(self, dsn: str, min_size: int = 2, max_size: int = 10):
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            self.dsn,
            min_size=self.min_size,
            max_size=self.max_size,
            # 注册 pgvector 类型
            init=self._init_connection,
        )

    async def _init_connection(self, conn: asyncpg.Connection) -> None:
        # pgvector 需要注册自定义类型
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        # 注册 vector 类型编解码器
        from pgvector.asyncpg import register_vector
        await register_vector(conn)

    @property
    def pool(self) -> asyncpg.Pool:
        assert self._pool is not None, "Call connect() first"
        return self._pool

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
```

### 本地开发 vs K8s 部署

```
本地开发（零配置启动）：
$ docker run -d --name coding-agent-db \
    -e POSTGRES_PASSWORD=dev \
    -p 5432:5432 \
    pgvector/pgvector:pg16
$ export DATABASE_URL="postgresql://postgres:dev@localhost:5432/postgres"

K8s 部署（CloudNativePG Operator）：
$ helm install cnpg cloudnative-pg/cloudnative-pg -n cnpg-system --create-namespace
$ kubectl apply -f k8s/postgresql-cluster.yaml
```

```yaml
# k8s/postgresql-cluster.yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: coding-agent-db
spec:
  instances: 3
  imageName: ghcr.io/cloudnative-pg/postgresql:16
  bootstrap:
    initdb:
      postInitTemplateSQL:
        - CREATE EXTENSION IF NOT EXISTS vector;
  storage:
    size: 5Gi
    storageClass: standard
  resources:
    requests:
      memory: "512Mi"
      cpu: "250m"
    limits:
      memory: "1Gi"
      cpu: "500m"
  monitoring:
    enablePodMonitor: true
```

### 迁移路径（从 SQLite + LanceDB）

| 步骤 | 操作 | 影响范围 |
|------|------|---------|
| 1 | 本地启动 PostgreSQL Docker，创建 schema | 零影响，新增 |
| 2 | 实现 `PostgresTapeStore`，新 Tape 数据写入 PG | 新模块，不改旧代码 |
| 3 | 实现 `PgVectorDocIndex`，替换 `kb.py` 内部实现 | kb.py 接口不变，只换底层 |
| 4 | Session 从 SQLite 迁移到 PG | 改 `core/session.py` 内部实现 |
| 5 | 移除 LanceDB + SQLite 依赖 | 清理 pyproject.toml |

---

## 六、声明式存储后端接入

### 设计目标

像 Bub 一样，存储后端可以自由替换，但**不用 pluggy**——用 Registry 模式 + TOML 配置驱动 Factory。

Bub 用 pluggy 的 Hook-first 模式实现可替换性：框架定义 hookspec，plugin 实现 hookimpl，通过 entry_points 或 `register()` 动态注册。这对 ~200 行的极小内核是合理的。但你的项目已有 14,082 行代码，引入 pluggy 需要把所有模块间调用改造为 hook 调用——成本过高。

**Registry + Config** 模式实现同样的声明式效果，但零框架依赖：

| 对比 | Bub (pluggy) | 本设计 (Registry) |
|------|-------------|-------------------|
| 注册方式 | `@hookimpl` + entry_points | `@register("tape", "postgres")` |
| 配置方式 | Python 代码 / CLI 参数 | `agent.toml` 声明式 |
| 发现机制 | pluggy 的 plugin manager | 装饰器自注册 + 可选 `importlib` |
| 运行时替换 | ✅ 支持 | ❌ 启动时确定（够用） |
| 引入成本 | 重写调用链 | 零——只加装饰器 |

### 核心机制：三层结构

```
agent.toml                  BackendRegistry              具体实现
(声明用什么)                 (名字→类的映射)              (@register 注册)

[storage.tape]          ┌─ "tape" ─────────────┐
backend = "postgres"  → │   "jsonl"    → JSONLTapeStore
dsn = "postgresql://…"  │   "postgres" → PostgresTapeStore  ← 命中
                        └──────────────────────┘

[storage.doc_index]     ┌─ "doc_index" ─────────┐
backend = "pgvector"  → │   "pgvector" → PgVectorDocIndex   ← 命中
dsn = "postgresql://…"  │   "null"     → NullDocIndex
                        └───────────────────────┘

[storage.session]       ┌─ "session" ───────────┐
backend = "memory"    → │   "postgres" → PostgresSessionStore
                        │   "memory"   → InMemorySessionStore ← 命中
                        │   "redis"    → RedisSessionStore（用户自定义）
                        └───────────────────────┘
```

### Registry 实现（~40 行）

```python
class BackendRegistry:
    """全局后端注册表。namespace → name → class。"""

    _registry: dict[str, dict[str, type]] = {}

    @classmethod
    def register(cls, namespace: str, name: str):
        """装饰器：注册一个后端实现。"""
        def decorator(impl_class: type) -> type:
            cls._registry.setdefault(namespace, {})[name] = impl_class
            return impl_class
        return decorator

    @classmethod
    def get(cls, namespace: str, name: str) -> type:
        """按名字查找实现类。"""
        try:
            return cls._registry[namespace][name]
        except KeyError:
            available = list(cls._registry.get(namespace, {}).keys())
            raise KeyError(
                f"Unknown backend {name!r} for {namespace!r}. "
                f"Available: {available}"
            )

    @classmethod
    def available(cls, namespace: str) -> list[str]:
        return list(cls._registry.get(namespace, {}).keys())

# 简写
register = BackendRegistry.register
```

### 内置后端注册（每个实现文件顶部一行装饰器）

```python
# tape/jsonl_store.py
@register("tape", "jsonl")
class JSONLTapeStore:
    def __init__(self, *, data_dir: str = "./data/tapes", **_kwargs):
        self.data_dir = data_dir
    async def append(self, session_id, entry): ...
    async def entries(self, session_id, since_anchor=None): ...
    ...

# tape/postgres_store.py
@register("tape", "postgres")
class PostgresTapeStore:
    def __init__(self, *, dsn: str, pool_min: int = 2, pool_max: int = 10, **_kwargs):
        self.dsn = dsn
        ...
    async def append(self, session_id, entry): ...
    ...

# index/pgvector_doc_index.py
@register("doc_index", "pgvector")
class PgVectorDocIndex:
    def __init__(self, *, dsn: str, embedding_model: str = "text-embedding-3-small", **_kwargs):
        ...

# index/null_doc_index.py
@register("doc_index", "null")
class NullDocIndex:
    """空实现——不做向量检索，用于纯 Tree-sitter 模式。"""
    def __init__(self, **_kwargs): pass
    async def search(self, query, top_k=5): return []
    async def upsert(self, doc_id, content, source): pass
```

**关键设计点**：每个 `__init__` 都接受 `**_kwargs`，这样 TOML 中的额外配置字段不会导致 TypeError，也方便未来扩展参数。

### 配置文件：agent.toml

```toml
# ============================
# 本地开发（零依赖）
# ============================
[storage.tape]
backend = "jsonl"
data_dir = "./data/tapes"

[storage.doc_index]
backend = "null"

[storage.session]
backend = "memory"
```

```toml
# ============================
# 生产部署（PostgreSQL 全家桶）
# ============================
[storage.tape]
backend = "postgres"
dsn = "postgresql://postgres:secret@db:5432/coding_agent"
pool_min = 5
pool_max = 20

[storage.doc_index]
backend = "pgvector"
dsn = "postgresql://postgres:secret@db:5432/coding_agent"
embedding_model = "text-embedding-3-small"

[storage.session]
backend = "postgres"
dsn = "postgresql://postgres:secret@db:5432/coding_agent"
```

```toml
# ============================
# 混合模式（自由组合）
# ============================
[storage]
plugins = ["my_company.agent_backends"]   # 加载外部后端模块

[storage.tape]
backend = "postgres"
dsn = "postgresql://localhost:5432/agent"

[storage.doc_index]
backend = "pgvector"
dsn = "postgresql://localhost:5432/agent"

[storage.session]
backend = "redis"        # 用户自定义后端
url = "redis://localhost:6379"
prefix = "agent:session:"
```

### 配置驱动的 Factory（~30 行）

```python
import tomllib

def create_agent_from_config(config_path: str = "agent.toml") -> AgentLoop:
    """一行启动：从配置文件创建完整的 AgentLoop。"""
    with open(config_path, "rb") as f:
        config = tomllib.load(f)

    storage_config = config["storage"]

    # 可选：加载外部插件模块（触发 @register 装饰器）
    if "plugins" in storage_config:
        import importlib
        for module_path in storage_config.pop("plugins"):
            importlib.import_module(module_path)

    # 按配置创建每个后端
    backends = {}
    for namespace, cfg in storage_config.items():
        cfg = dict(cfg)  # 拷贝，避免修改原 config
        backend_name = cfg.pop("backend")
        impl_class = BackendRegistry.get(namespace, backend_name)
        backends[namespace] = impl_class(**cfg)

    # 组装 AgentLoop
    code_index = TreeSitterIndex(config.get("project", {}).get("root", "."))
    memory = DefaultMemoryManager(
        tape=backends["tape"],
        code_index=code_index,
        doc_index=backends["doc_index"],
        grounding_path=config.get("project", {}).get("agents_md", "AGENTS.md"),
    )
    return AgentLoop(
        tape_store=backends["tape"],
        code_index=code_index,
        doc_index=backends["doc_index"],
        session_store=backends["session"],
        memory=memory,
    )
```

### 用户自定义后端：两步接入

**步骤 1**：写一个类，实现 Protocol 接口，加 `@register` 装饰器。

```python
# my_company/agent_backends.py
from coding_agent.registry import register

@register("session", "redis")
class RedisSessionStore:
    def __init__(self, *, url: str = "redis://localhost:6379", prefix: str = "session:", **_kwargs):
        self.url = url
        self.prefix = prefix

    async def get(self, session_id):
        import redis.asyncio as redis
        r = redis.from_url(self.url)
        data = await r.get(f"{self.prefix}{session_id}")
        return json.loads(data) if data else None

    async def save(self, session_id, metadata):
        import redis.asyncio as redis
        r = redis.from_url(self.url)
        await r.set(f"{self.prefix}{session_id}", json.dumps(metadata))
```

**步骤 2**：在 `agent.toml` 中声明。

```toml
[storage]
plugins = ["my_company.agent_backends"]

[storage.session]
backend = "redis"
url = "redis://localhost:6379"
prefix = "agent:session:"
```

完成。不改 AgentLoop 一行代码，不改 factory 一行代码。

### Protocol 验证（可选的运行时安全检查）

```python
def create_backend_checked(namespace: str, cfg: dict, protocol: type) -> Any:
    """创建后端并验证它实现了对应的 Protocol。"""
    cfg = dict(cfg)
    backend_name = cfg.pop("backend")
    impl_class = BackendRegistry.get(namespace, backend_name)
    instance = impl_class(**cfg)

    if not isinstance(instance, protocol):
        missing = [m for m in dir(protocol) if not m.startswith("_")
                   and not hasattr(instance, m)]
        raise TypeError(
            f"{impl_class.__name__} does not implement {protocol.__name__}. "
            f"Missing: {missing}"
        )
    return instance
```

这样即使用户的自定义后端少实现了某个方法，启动时就会报明确的错误，而不是运行到一半才崩。

---

## 七、与现有模块的对接关系

```
现有模块                    变更                          新增模块
─────────────────────────────────────────────────────────────────────────
core/context.py          改造：感知 anchor，从 anchor 重建    ←→  tape/store.py (TapeStore)
                                                              tape/entry.py (Entry/Anchor)
                                                              tape/triage.py (TriageGate)

core/session.py          改造：底层从 SQLite 换为 PostgreSQL   ←→  storage/database.py (DatabasePool)

core/planner.py          改造：plan 输出写入 Tape anchor      ←→  tape/store.py

summarizer/              改造：输出对齐到 anchor payload       ←→  tape/entry.py

kb.py                    改造：底层从 LanceDB 换为 pgvector    ←→  index/doc_index.py (PgVectorDocIndex)

（无）                    新增                                  index/code_index.py (Tree-sitter)
                                                              storage/database.py (DatabasePool)
                                                              storage/migrations.py (Schema 管理)

agents/subagent.py       改造：使用 tape fork/merge             ←→  tape/store.py

wire/protocol.py         不动
ui/                      不动
approval/                不动
skills/                  不动（Skill 文档由 pgvector 索引）
```

---

## 八、新增文件清单

```
src/coding_agent/
├── registry.py               # 新增：BackendRegistry + @register 装饰器（~40 行）
├── protocols.py              # 新增：所有 Protocol 定义集中管理
├── tape/                     # 新增：Tape 子系统
│   ├── __init__.py
│   ├── entry.py              # Entry, EntryKind, AnchorPayload 数据结构
│   ├── jsonl_store.py        # @register("tape", "jsonl") — 本地开发用
│   ├── postgres_store.py     # @register("tape", "postgres") — 生产用
│   └── triage.py             # TriageGate 过滤逻辑
├── storage/                  # 新增：统一存储层
│   ├── __init__.py
│   ├── database.py           # DatabasePool（asyncpg 连接池 + pgvector 注册）
│   └── migrations.py         # Schema 创建/迁移（tape_entries, sessions, doc_embeddings）
├── index/                    # 新增：知识索引子系统
│   ├── __init__.py
│   ├── code_index.py         # CodeIndex Protocol + TreeSitterIndex
│   ├── pgvector_doc_index.py # @register("doc_index", "pgvector")
│   └── null_doc_index.py     # @register("doc_index", "null")
├── memory/                   # 新增：记忆管理
│   ├── __init__.py
│   └── manager.py            # MemoryManager：组装 Grounding + Tape + Index
├── factory.py                # 新增：配置驱动的 Factory（create_agent_from_config）
├── AGENTS.md                 # 新增：Grounding Layer 文件
├── agent.toml                # 新增：声明式后端配置
└── ...（现有模块不动）

k8s/                          # 新增：K8s 部署配置
├── postgresql-cluster.yaml   # CloudNativePG 集群定义
└── coding-agent.yaml         # Agent 应用 Deployment
```

新增依赖：`asyncpg`、`pgvector`（Python 客户端）、`tree-sitter`、`tree-sitter-python`
移除依赖：`lancedb`、`aiosqlite`（或项目中对应的 SQLite 异步库）

预估新增代码量：~1,500-1,800 行（含测试和 schema 迁移）。

---

## 九、实施顺序

### Week 1：存储层 + Tape 基础

1. 启动 PostgreSQL Docker，编写 `storage/migrations.py` 创建 schema
2. 实现 `DatabasePool`（asyncpg 连接池 + pgvector 类型注册）
3. 定义 `Entry`、`EntryKind`、`AnchorPayload` 数据结构
4. 实现 `PostgresTapeStore`（append / entries / anchors）+ `JSONLTapeStore`（本地回退）
5. 在 `AgentLoop` 中接入 Tape——每个 action 自动写入 entry
6. 创建 `AGENTS.md` 模板

**验收标准**：跑一轮完整的 coding 任务，PostgreSQL `tape_entries` 表包含完整操作日志。

### Week 2：Anchor + 上下文重建 + Session 迁移

1. 在 `planner.py` 的阶段转换处写入 anchor
2. 改造 `context.py`：从最近 anchor 开始重建上下文
3. 实现 `TriageGate`（纯规则版，不调 LLM）
4. 将 `core/session.py` 底层从 SQLite 切换到 PostgreSQL
5. 补充 Tape + Session 相关测试

**验收标准**：长对话场景下，上下文从 anchor 重建而非线性截取，token 使用量下降。Session 数据存在 PostgreSQL 中。

### Week 3-4：Tree-sitter 代码索引 + pgvector 文档索引

1. 引入 `tree-sitter` + `tree-sitter-python`
2. 实现 `TreeSitterIndex`（解析 + 符号图 + PageRank + 查询）
3. 实现 `PgVectorDocIndex`，替换 `kb.py` 内部的 LanceDB 调用
4. 将代码检索走 `TreeSitterIndex`，文档检索走 `PgVectorDocIndex`
5. 移除 LanceDB + SQLite 依赖

**验收标准**：代码相关问题的检索精度提升；文档检索通过 pgvector 完成；`pyproject.toml` 中无 lancedb 依赖。

### 后续（不急）

- Tape fork/merge 对接 SubAgent
- anchor 摘要异步写入 pgvector（跨 session 检索）
- 基础衰减评分（在 `doc_embeddings` 上加 `relevance_score` 列）
- Memory Consolidation 后台任务
- K8s 部署：编写 CloudNativePG Cluster YAML + Agent Deployment

---

## 附录：关键概念对照表

| 概念 | Bub 框架 | Kapy 模型 | Nowledge | 本设计 |
|------|---------|-----------|---------|--------|
| 固定注入层 | AGENTS.md + system prompt | Grounding（一级记忆） | Working Memory 晨间简报 | **Layer 0: AGENTS.md** |
| 操作日志 | Tape（append-only + anchor） | — | Trace（原始对话） | **Layer 1: Tape** |
| 结构化知识 | — | Getter（二级记忆） | Unit（原子知识） | **Anchor payload** |
| 持久检索 | tape.search | Getter tool call | 快路径/深路径检索 | **Layer 2: Knowledge Store** |
| 知识演化 | — | — | replaces/enriches/confirms/challenges | **Anchor.evolves 字段** |
| 知识结晶 | — | — | Crystal（3+ 来源验证） | **不做**（当前阶段过重） |
| 遗忘 | — | — | 衰减 + 置信度双分数 | **后续加**（基础衰减评分） |
| 插件系统 | pluggy Hook-first | — | MCP | **Registry + TOML 声明式** |
| 存储后端 | FileTapeStore（JSONL 文件） | — | 本地图数据库 + 向量 | **可插拔（默认 PostgreSQL + pgvector）** |
| K8s 部署 | 无（本地框架） | — | 桌面端/CLI/MCP | **CloudNativePG Operator** |
