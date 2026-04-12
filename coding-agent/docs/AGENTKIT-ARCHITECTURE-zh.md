# AgentKit 架构

> 一个面向 Python 的钩子驱动、插件化 AI Agent 运行时框架。

## 1. 概述

AgentKit 是驱动编程 Agent 的基础框架，提供以下能力：

- **插件化可扩展性** — 所有行为均通过实现钩子契约的插件注入
- **协议驱动设计** — 核心抽象使用 Python `Protocol` 类型（结构化子类型，无需继承）
- **类型安全的流水线** — 7 阶段线性流水线，贯穿类型化上下文
- **只追加的对话历史** — 线程安全的 `Tape`，支持窗口化和 JSONL 持久化
- **Provider 抽象** — LLM Provider 可通过异步流式协议插拔

### 设计哲学

AgentKit 将**机制**与**策略**分离。框架提供执行流水线、钩子分发和对话管理；所有领域特定行为（使用哪个 LLM、暴露哪些工具、如何审批工具调用）均委托给插件。

```
┌─────────────────────────────────────────┐
│            应用代码层                    │
│  (coding_agent, 自定义 Agent 等)         │
├─────────────────────────────────────────┤
│              AgentKit API               │
│  Pipeline · Plugins · Tape · Tools      │
├─────────────────────────────────────────┤
│           钩子运行时层                   │
│  HookRuntime · HookSpec · Registry      │
├─────────────────────────────────────────┤
│         Provider / 存储层               │
│  LLMProvider · TapeStore · SessionStore │
└─────────────────────────────────────────┘
```

---

## 2. 模块结构

```
src/agentkit/
├── __init__.py              # 公共 API 再导出
├── _types.py                # StageName、EntryKind 类型别名
├── errors.py                # 错误层级体系
├── tracing.py               # 可观测性工具
├── py.typed                 # PEP 561 标记
│
├── channel/                 # 双向通信
│   ├── protocol.py          #   Channel 协议
│   └── local.py             #   内存 LocalChannel
│
├── config/                  # 配置
│   └── loader.py            #   TOML 加载，AgentConfig 数据类
│
├── context/                 # LLM 消息组装
│   └── builder.py           #   ContextBuilder
│
├── directive/               # 控制流效果
│   ├── types.py             #   Directive 数据类
│   └── executor.py          #   DirectiveExecutor
│
├── instruction/             # 输入规范化
│   └── normalize.py         #   normalize_instruction()
│
├── plugin/                  # 插件系统
│   ├── protocol.py          #   Plugin 协议
│   └── registry.py          #   PluginRegistry
│
├── providers/               # LLM Provider 抽象
│   ├── protocol.py          #   LLMProvider 协议
│   └── models.py            #   StreamEvent 类型
│
├── runtime/                 # 执行引擎
│   ├── hookspecs.py         #   14 个钩子规格
│   ├── hook_runtime.py      #   HookRuntime 分发器
│   └── pipeline.py          #   7 阶段 Pipeline
│
├── storage/                 # 持久化协议
│   ├── protocols.py         #   TapeStore、DocIndex、SessionStore
│   └── session.py           #   SessionStore 实现
│
├── tape/                    # 对话历史
│   ├── models.py            #   Entry 数据类
│   ├── store.py             #   TapeStore、ForkTapeStore
│   └── tape.py              #   Tape 类
│
└── tools/                   # 工具系统
    ├── decorator.py          #   @tool 装饰器
    ├── registry.py           #   ToolRegistry
    └── schema.py             #   ToolSchema 数据类
```

---

## 3. 核心抽象

### 3.1 Pipeline 与 PipelineContext

`Pipeline` 是核心执行引擎。它将 Agent 的一次**轮次（turn）**通过 7 个顺序阶段运行，并贯穿一个可变的 `PipelineContext`。

```python
@dataclass
class PipelineContext:
    tape: Tape                              # 对话历史
    session_id: str                         # 当前会话
    config: dict[str, Any]                  # 运行时配置
    plugin_states: dict[str, Any]           # 各插件可变状态
    messages: list[dict[str, Any]]          # 已组装的 LLM 消息
    llm_provider: Any                       # 当前 LLM Provider
    storage: Any                            # 当前 Tape 存储
    tool_schemas: list[Any]                 # 可用工具定义
    response_entries: list[Any]             # 当前轮次产生的条目
    output: Any                             # 阶段输出（指令）
    on_event: Callable | None               # 流式事件回调
```

```python
class Pipeline:
    STAGES = [
        "resolve_session", "load_state", "build_context",
        "run_model", "save_state", "render", "dispatch"
    ]

    async def run_turn(self, ctx: PipelineContext) -> PipelineContext:
        # 按顺序执行各阶段
        # 用 ForkTapeStore 包装存储以确保事务安全
        # 发生错误时：回滚 tape，通知 on_error 观察者
```

### 3.2 Entry 与 Tape

`Entry` 是对话历史的原子单元，`Tape` 是线程安全的有序集合。

```python
@dataclass(frozen=True)
class Entry:
    kind: EntryKind       # "message" | "tool_call" | "tool_result" | "summary" | ...
    payload: dict         # 角色相关内容
    id: str               # UUID
    timestamp: float      # Unix 时间戳
    meta: dict            # 可扩展元数据（anchor_type、is_handoff 等）
```

```python
class Tape:
    # 线程安全（Lock 保护）的只追加对话日志
    def append(entry: Entry)               # 添加条目
    def windowed_entries() -> list[Entry]  # 从 window_start 开始的条目
    def handoff(summary_anchor, ...)       # 摘要后推进窗口
    def fork() -> Tape                     # 事务性分叉
    def save_jsonl(path) / load_jsonl(path)  # 持久化
```

**窗口模型：**

```
完整 tape：[e0] [e1] [e2] [summary] [e4] [e5] [e6]
                                ↑
                          window_start=3
可见部分：                [summary] [e4] [e5] [e6]
```

旧条目被保留但不参与上下文构建。`handoff()` 方法插入摘要锚点并推进窗口。

### 3.3 Plugin 协议

```python
@runtime_checkable
class Plugin(Protocol):
    state_key: str                                    # 唯一命名空间 ID
    def hooks(self) -> dict[str, Callable[..., Any]]  # hook_name → callable
```

任何满足此结构协议的对象即为合法插件，无需继承。

### 3.4 指令（Directives）

指令是不可变的值对象，描述由 `DirectiveExecutor` 执行的**副作用**：

```python
class Directive:             # 抽象基类
class Approve(Directive)     # 工具调用已批准
class Reject(Directive)      # 工具调用已拒绝（reason: str）
class AskUser(Directive)     # 暂停等待用户输入（question: str）
class Checkpoint(Directive)  # 持久化插件状态
class MemoryRecord(Directive)  # 存储记忆（summary, tags, importance）
```

### 3.5 ToolSchema 与 ToolRegistry

```python
@dataclass
class ToolSchema:
    name: str
    description: str
    parameters: dict          # JSON Schema
    def to_openai_format()    # 转换为 OpenAI function calling 格式

class ToolRegistry:
    def register(schema, handler)  # 注册工具
    def get(name) -> handler       # 查找处理器
    def schemas() -> list          # 所有已注册的 Schema
```

`@tool` 装饰器可从函数签名和文档字符串自动生成 `ToolSchema`。

---

## 4. 插件系统

### 4.1 钩子规格

AgentKit 在 `HOOK_SPECS` 注册表中定义了 **14 个钩子**。每个 `HookSpec` 声明：

| 字段 | 用途 |
|---|---|
| `name` | 钩子标识符 |
| `firstresult` | 遇到第一个非 None 结果即停止（call_first） |
| `is_observer` | 即发即忘，吞掉错误（notify） |
| `returns_directive` | 返回值为 Directive |
| `return_type` | 用于验证的预期返回类型 |

### 4.2 14 个钩子

| # | 钩子 | 模式 | 阶段 | 用途 |
|---|---|---|---|---|
| 1 | `provide_storage` | first_result | load_state | 返回 TapeStore 实例 |
| 2 | `get_tools` | collect_all | load_state | 返回 ToolSchema 列表 |
| 3 | `provide_llm` | first_result | load_state | 返回 LLMProvider 实例 |
| 4 | `approve_tool_call` | first_result | run_model | 返回 Approve/Reject/AskUser |
| 5 | `summarize_context` | first_result | build_context | 遗留：压缩 tape 条目 |
| 6 | `resolve_context_window` | first_result | build_context | 返回 (window_start, summary_anchor) |
| 7 | `on_error` | observer | any | 流水线错误时通知 |
| 8 | `mount` | collect_all | init | 插件初始化，返回状态 |
| 9 | `on_checkpoint` | observer | save_state | 在轮次边界持久化状态 |
| 10 | `build_context` | collect_all | build_context | 注入 grounding 上下文（记忆、知识库） |
| 11 | `on_turn_end` | collect_all | render | 产生 MemoryRecord 指令 |
| 12 | `execute_tool` | first_result | run_model | 执行单个工具调用 |
| 13 | `on_session_event` | observer | any | 会话级事件（topic、handoff） |
| 14 | `execute_tools_batch` | first_result | run_model | 并行批量执行工具 |

### 4.3 钩子分发模式

```
call_first(hook, **kwargs)   → 返回第一个非 None 结果（短路）
call_many(hook, **kwargs)    → 收集所有结果为列表
notify(hook, **kwargs)       → 即发即忘，吞掉异常
```

### 4.4 注册流程

```
1. 创建 PluginRegistry(specs=HOOK_SPECS)
2. registry.register(plugin)
   → 验证 state_key 唯一性
   → 将 plugin.hooks() 与已知规格建立索引
   → 对未知钩子名称发出警告
3. 创建 HookRuntime(registry, specs)
   → 准备好调用 call_first/call_many/notify
```

---

## 5. 流水线阶段

### 5.1 阶段流程图

```
用户输入
    │
    ▼
┌─────────────────┐
│ resolve_session  │  会话初始化（当前为空操作）
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   load_state    │  provide_storage → provide_llm → get_tools
│                 │  收集：storage, llm_provider, tool_schemas
│                 │  开始 ForkTapeStore 事务
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  build_context  │  resolve_context_window → build_context 钩子
│                 │  ContextBuilder.build(tape, grounding)
│                 │  → ctx.messages（已准备好供 LLM 使用）
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   run_model     │  LLM 流式循环（最多 max_tool_rounds 轮）：
│                 │    stream() → TextEvent/ToolCallEvent/DoneEvent
│                 │    对每个 tool_call：
│                 │      approve_tool_call → execute_tool(s)
│                 │      将 tool_result 追加到 tape
│                 │      重新运行 build_context，继续循环
│                 │    纯文本响应 → 退出
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   save_state    │  on_checkpoint（观察者）→ 持久化状态
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│     render      │  on_turn_end → 收集 Directive 列表
│                 │  执行指令（MemoryRecord 等）
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│    dispatch     │  最终分发（当前为空操作）
└─────────────────┘
```

### 5.2 run_model 工具循环详解

```
for round in range(max_tool_rounds):
    ┌─────────────────────────────┐
    │  stream(messages, tools)    │  StreamEvent 的异步迭代器
    │  ├─ TextEvent → 缓冲        │
    │  ├─ ThinkingEvent → 缓冲    │
    │  ├─ ToolCallEvent → 队列    │
    │  └─ DoneEvent → 退出        │
    └──────────┬──────────────────┘
               │
    ┌──────────▼──────────────────┐
    │  仅有文本? → 追加 Entry     │──→ BREAK（轮次完成）
    │           (kind="message")  │
    └──────────┬──────────────────┘
               │ 有 tool_calls
               ▼
    ┌─────────────────────────────┐
    │ 对每个 tool_call：           │
    │   approve_tool_call(...)    │
    │   ├─ Approve → 执行          │
    │   └─ Reject → 跳过           │
    └──────────┬──────────────────┘
               │
    ┌──────────▼──────────────────┐
    │ 批量或顺序执行               │
    │ execute_tools_batch (≥2)    │
    │ execute_tool（单个）         │
    │ → 追加 tool_result Entry    │
    │ → 触发 ToolResultEvent      │
    └──────────┬──────────────────┘
               │
    ┌──────────▼──────────────────┐
    │  重新运行 build_context      │
    │  → 更新 ctx.messages        │
    │  → 继续循环                  │
    └─────────────────────────────┘
```

### 5.3 事务安全

流水线将 tape 的变更操作包裹在 `ForkTapeStore` 事务中：

```
load_state：
    fork = storage.begin(tape)   # 创建 tape 分叉
    ctx.tape = fork              # 所有变更写入分叉

成功时：
    storage.commit(fork)         # 持久化到底层存储

出错时：
    storage.rollback(fork)       # 丢弃分叉
    ctx.tape = original_tape     # 恢复原始 tape
```

---

## 6. Provider 抽象

### 6.1 LLMProvider 协议

```python
@runtime_checkable
class LLMProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def max_context_size(self) -> int: ...

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]: ...
```

任何拥有这三个成员的对象都是合法的 Provider。`stream()` 方法是一个异步生成器，产出 `StreamEvent` 子类型。

### 6.2 StreamEvent 类型

```python
@dataclass(frozen=True)
class TextEvent:           # text: str — 增量内容
class ThinkingEvent:       # text: str — 推理/思维链
class ToolCallEvent:       # tool_call_id, name, arguments
class ToolResultEvent:     # tool_call_id, name, result, is_error
class DoneEvent:           # stop_reason: str, usage: dict
```

```
LLM 流：  [Think] [Think] [Text] [Text] [ToolCall] [ToolCall] [Done]
             │       │      │      │        │          │         │
             ▼       ▼      ▼      ▼        ▼          ▼         ▼
         on_event 回调 → 转发至 PipelineAdapter → Wire → UI
```

---

## 7. 存储与持久化

### 7.1 存储协议

```python
class TapeStore(Protocol):
    def save(tape: Tape) -> None
    def load(tape_id: str) -> Tape | None
    def list_ids() -> list[str]

class SessionStore(Protocol):
    def save_session(session_id, metadata) -> None
    def load_session(session_id) -> dict | None

class DocIndex(Protocol):
    def search(query: str, limit: int) -> list[dict]
```

### 7.2 ForkTapeStore

为流水线执行提供事务语义：

```python
class ForkTapeStore:
    def begin(tape: Tape) -> Tape      # 创建分叉
    def commit(fork: Tape) -> None     # 持久化到底层存储
    def rollback(fork: Tape) -> None   # 丢弃分叉
```

### 7.3 JSONL 格式

Tape 以换行符分隔的 JSON 格式持久化：

```jsonl
{"id":"abc","kind":"message","payload":{"role":"user","content":"Hello"},"timestamp":1712000000}
{"id":"def","kind":"message","payload":{"role":"assistant","content":"Hi!"},"timestamp":1712000001}
{"id":"ghi","kind":"tool_call","payload":{"id":"tc1","name":"file_read","arguments":{"path":"x.py"},"role":"assistant"},"timestamp":1712000002}
{"id":"jkl","kind":"tool_result","payload":{"tool_call_id":"tc1","content":"...文件内容..."},"timestamp":1712000003}
```

支持增量追加——每次只写入自上次保存后的新条目。

---

## 8. Channel 系统

`Channel` 协议支持组件间的双向通信：

```python
class Channel(Protocol):
    async def send(message: Any) -> None
    async def receive() -> Any
    def subscribe(callback: Callable) -> str     # 返回订阅 ID
    def unsubscribe(sub_id: str) -> None
```

`LocalChannel` 使用 `asyncio.Queue` 和回调列表提供内存实现，用于进程内通信（例如流水线与 UI 之间）。

---

## 9. 指令系统

指令为流水线副作用实现了**命令模式**：

```
流水线阶段              产生的指令              DirectiveExecutor 动作
─────────────────────────────────────────────────────────────────────────
run_model               Approve                 允许工具执行
run_model               Reject(reason)          阻止工具，记录原因
run_model               AskUser(question)       暂停等待用户输入
save_state              Checkpoint(state)       持久化插件状态
render (on_turn_end)    MemoryRecord(summary)   存入知识库
```

`DirectiveExecutor` 将每种指令类型分发到对应的处理器：

```python
class DirectiveExecutor:
    async def execute(directive: Directive) -> bool:
        match directive:
            case Approve():      return True
            case Reject():       return False
            case AskUser():      # 提示用户
            case Checkpoint():   # 持久化状态
            case MemoryRecord(): # 存储记忆
```

---

## 10. 扩展点

| 扩展点 | 机制 | 示例 |
|---|---|---|
| **添加工具** | 实现 `get_tools` 钩子的插件 | 文件操作、Shell、搜索 |
| **自定义 LLM** | 实现 `provide_llm` 钩子的插件 | Anthropic、OpenAI、本地模型 |
| **存储后端** | 实现 `provide_storage` 钩子的插件 | SQLite、S3、本地文件系统 |
| **工具审批** | 实现 `approve_tool_call` 钩子的插件 | 策略引擎、用户提示 |
| **上下文注入** | 实现 `build_context` 钩子的插件 | RAG、记忆、知识库 |
| **上下文窗口** | 实现 `resolve_context_window` 钩子的插件 | 基于 Topic、基于 Token |
| **轮次结束效果** | 实现 `on_turn_end` 钩子的插件 | 记忆记录、指标 |
| **并行执行** | 实现 `execute_tools_batch` 钩子的插件 | 异步批处理器 |
| **可观测性** | 实现观察者钩子的插件 | 指标、错误追踪 |
| **自定义指令** | 继承 `Directive` + 执行器处理器 | 领域特定效果 |

---

## 11. 依赖关系图

```
                    ┌──────────┐
                    │ __init__ │  （再导出）
                    └────┬─────┘
                         │
         ┌───────────────┼───────────────┐
         │               │               │
    ┌────▼────┐    ┌─────▼─────┐   ┌─────▼─────┐
    │ runtime │    │  plugin   │   │   tape    │
    │         │    │           │   │           │
    │pipeline │◄───│ registry  │   │  tape.py  │
    │hookspecs│    │ protocol  │   │ models.py │
    │hook_rt  │    └───────────┘   │ store.py  │
    └────┬────┘                    └─────┬─────┘
         │                               │
    ┌────▼────────┐              ┌───────▼───────┐
    │  providers  │              │   storage     │
    │  protocol   │              │  protocols    │
    │  models     │              │  session      │
    └─────────────┘              └───────────────┘
         │
    ┌────▼────┐    ┌───────────┐    ┌───────────┐
    │ context │    │ directive │    │   tools   │
    │ builder │    │  types    │    │ decorator │
    └─────────┘    │ executor  │    │ registry  │
                   └───────────┘    │ schema    │
                                    └───────────┘
    ┌───────────┐    ┌───────────┐    ┌───────────┐
    │  channel  │    │  config   │    │instruction│
    │ protocol  │    │  loader   │    │ normalize │
    │  local    │    └───────────┘    └───────────┘
    └───────────┘

    ┌───────────┐    ┌───────────┐
    │  _types   │    │  errors   │  ← 被所有模块使用
    └───────────┘    └───────────┘
```

### 关键依赖规则

- `runtime/pipeline.py` 依赖：`plugin.registry`、`providers.models`、`tape`、`directive.types`、`context.builder`
- `plugin/` **不**依赖 `runtime/`（清晰分离）
- `tape/` 仅依赖 `_types`（完全自包含）
- `providers/` 仅依赖自身的 `models.py`（协议是结构化的）
- `errors.py` 和 `_types.py` 是叶子依赖（被所有地方使用，自身不依赖任何模块）

---

## 12. 错误层级

```
AgentKitError
├── PipelineError     # 阶段执行失败
├── HookError         # 钩子分发失败
├── PluginError       # 插件注册/状态错误
├── DirectiveError    # 指令执行失败
├── StorageError      # 持久化失败
├── ToolError         # 工具执行失败
└── ConfigError       # 配置加载失败
```

所有错误均携带足够的上下文信息（阶段名、插件 ID、工具名），便于排查问题。
