# Coding Agent 架构

> 基于 AgentKit 构建的分层 CLI 应用，用于交互式 AI 辅助编程。

## 1. 概述

`coding_agent` 是以 `agentkit` 为框架的应用层，提供以下能力：

- **多模式 CLI** — 通过 Click 实现交互式 REPL、批处理和 HTTP Server 模式
- **适配器模式** — `PipelineAdapter` 将 agentkit 流水线事件转换为类型化的 Wire 协议
- **Wire 协议** — 类型化数据类（`StreamDelta`、`ToolCallDelta`、`TurnEnd` 等）将 Agent 逻辑与展示层解耦
- **可插拔 Provider** — 支持 Anthropic、OpenAI 兼容、GitHub Copilot 以及 Kimi 系列后端
- **富文本 TUI** — 基于滚动缓冲区的流式渲染，使用 `prompt_toolkit` + `rich`
- **13 个插件** — 所有领域行为均通过 agentkit 的钩子系统注入，并包含 skills 与 MCP 集成

### 与 AgentKit 的关系

```
┌─────────────────────────────────────────────────────┐
│                  coding_agent                        │
│  CLI · Adapter · Wire · Providers · Plugins · UI    │
├─────────────────────────────────────────────────────┤
│                    agentkit                          │
│  Pipeline · Hooks · Tape · Tools · Directives       │
└─────────────────────────────────────────────────────┘
```

AgentKit 提供**机制**（流水线执行、钩子分发、对话 Tape）。Coding Agent 提供**策略**（使用哪个 LLM、哪些工具、如何渲染、何时审批）。

---

## 2. 分层架构

应用遵循严格的分层依赖图，每层只依赖下方的层。

```
┌─────────────────────────────────────────────────┐
│                CLI 层                            │
│  __main__.py · repl.py · input_handler.py       │
│  commands.py · bash_executor.py                 │
├─────────────────────────────────────────────────┤
│                UI 层                             │
│  stream_renderer.py · rich_consumer.py          │
│  approval_prompt.py · headless.py               │
│  http_server.py · components.py · theme.py      │
├─────────────────────────────────────────────────┤
│              适配器层                            │
│  adapter.py · adapter_types.py                  │
├─────────────────────────────────────────────────┤
│              Wire 协议                           │
│  wire/protocol.py · wire/local.py               │
├─────────────────────────────────────────────────┤
│             插件层                               │
│  实现 agentkit 钩子的 13 个插件                  │
├─────────────────────────────────────────────────┤
│            Provider 层                           │
│  base.py · anthropic.py · openai_compat.py      │
│  copilot.py                                     │
├─────────────────────────────────────────────────┤
│              Core 层                             │
│  config.py · context.py · session.py · tape.py  │
│  loop.py · doom.py · parallel.py · planner.py   │
├─────────────────────────────────────────────────┤
│             Tools 层                             │
│  file_ops.py · shell.py · planner.py            │
│  file_patch_tool.py · web_search.py · subagent.py │
├─────────────────────────────────────────────────┤
│              agentkit（框架）                    │
│  Pipeline · HookRuntime · Tape · ToolRegistry   │
└─────────────────────────────────────────────────┘
```

---

## 3. 模块结构

```
src/coding_agent/
├── __main__.py              # Click CLI：main, repl, run, stats, serve
├── adapter.py               # PipelineAdapter：agentkit 事件 → Wire 协议
├── adapter_types.py         # StopReason、TurnOutcome 数据类
├── agent.toml               # 默认 Agent 配置
│
├── approval/                # 审批策略框架
│   ├── policy.py            #   ApprovalPolicy 枚举，PolicyEngine
│   └── store.py             #   审批状态持久化
│
├── cli/                     # CLI 输入/输出层
│   ├── repl.py              #   InteractiveSession，REPL 循环
│   ├── input_handler.py     #   prompt_toolkit 按键绑定，Shell 模式
│   ├── commands.py          #   斜杠命令注册表（/help、/model 等）
│   ├── bash_executor.py     #   Shell 模式下的直接 Bash 执行
│   └── terminal_output.py   #   prompt_toolkit 的输出抽象
│
├── config/                  # 配置常量
│   └── constants.py         #   硬编码默认值
│
├── core/                    # 核心领域逻辑
│   ├── config.py            #   Pydantic Config 模型，分层加载
│   ├── context.py           #   对话上下文管理
│   ├── doom.py              #   Doom 循环检测逻辑
│   ├── loop.py              #   遗留 Agent 循环（流水线之前）
│   ├── parallel.py          #   并行执行工具
│   ├── planner.py           #   任务规划器（todo_write/todo_read）
│   ├── session.py           #   会话管理
│   └── tape.py              #   Tape 工具函数
│
├── plugins/                 # AgentKit 钩子实现（13 个插件）
│   ├── approval.py          #   approve_tool_call → Approve/Reject/AskUser
│   ├── core_tools.py        #   get_tools + execute_tool → ToolRegistry
│   ├── doom_detector.py     #   on_checkpoint → Doom 循环检测
│   ├── llm_provider.py      #   provide_llm → LLMProvider 工厂
│   ├── memory.py            #   build_context + on_turn_end → 记忆
│   ├── metrics.py           #   on_checkpoint → 性能指标
│   ├── mcp.py               #   mount/get_tools/execute_tool → MCP 服务工具
│   ├── parallel_executor.py #   execute_tools_batch → 并行执行
│   ├── shell_session.py     #   mount + on_checkpoint → Shell 状态
│   ├── skills.py            #   build_context + execute_tool → 技能发现与激活
│   ├── storage.py           #   provide_storage → JSONL TapeStore
│   ├── summarizer.py        #   resolve_context_window → 压缩
│   └── topic.py             #   on_checkpoint → 主题边界检测
│
├── providers/               # LLM Provider 实现
│   ├── base.py              #   ChatProvider 协议，StreamEvent，ToolCall
│   ├── anthropic.py         #   Anthropic Messages API
│   ├── openai_compat.py     #   OpenAI 兼容 Chat Completions
│   └── copilot.py           #   GitHub Copilot（继承 OpenAI-compat）
│
├── summarizer/              # 上下文摘要策略
│   ├── base.py              #   Summarizer 协议
│   ├── llm_summarizer.py    #   基于 LLM 的摘要（未来）
│   └── rule_summarizer.py   #   基于规则的截断
│
├── tools/                   # 工具实现
│   ├── file_ops.py          #   file_read, file_write, file_replace, glob, grep
│   ├── file_patch_tool.py   #   结构化文件补丁
│   ├── shell.py             #   带安全控制的 bash_run
│   ├── cache.py             #   工具结果缓存
│   ├── planner.py           #   todo_write, todo_read
│   ├── web_search.py        #   web_search 工具及后端封装
│   ├── subagent.py          #   子 Agent 委托工具
│   └── sandbox.py           #   sandbox 辅助函数
│
├── ui/                      # 展示层
│   ├── stream_renderer.py   #   StreamingRenderer：原始文本 → Rich 面板
│   ├── rich_consumer.py     #   RichConsumer：WireMessage → 渲染器调用
│   ├── approval_prompt.py   #   带预览的交互式审批 UI
│   ├── headless.py          #   HeadlessConsumer：基于日志的输出
│   ├── http_server.py       #   FastAPI HTTP Server 模式
│   ├── components.py        #   共享 Rich 组件
│   ├── theme.py             #   颜色/样式定义
│   ├── schemas.py           #   HTTP API 模式
│   ├── session_manager.py   #   HTTP 会话管理
│   ├── auth.py              #   HTTP API 认证
│   └── rate_limit.py        #   HTTP 限流
│
├── wire/                    # Wire 协议
│   ├── protocol.py          #   消息类型：StreamDelta, ToolCallDelta 等
│   └── local.py             #   LocalWire：基于异步队列的进程内 Wire
│
└── utils/                   # 共享工具函数
    └── retry.py             #   重试逻辑
```

---

## 4. 启动流程

### 入口点：`__main__.py`

CLI 使用 Click 并设置 `invoke_without_command=True`——不带子命令运行时进入 REPL 模式。

```
python -m coding_agent
       │
       ▼
  main() ─── 无子命令 ──→ _run_repl_command()
       │                            │
       ├── run  ──→ _run_headless() 或 _run_with_tui()
       ├── repl ──→ _run_repl_command()
       ├── stats ─→ collector.get_session()
       └── serve ─→ uvicorn.run(http_server.app)
```

### Agent 构建：`create_agent()`

`create_agent()` 工厂函数是连线中心，构建完整的 agentkit 流水线：

```python
def create_agent(...) -> tuple[Pipeline, PipelineContext]:
    # 1. 加载配置（TOML + 覆盖项）
    cfg = load_config(config_path)

    # 2. 用 agentkit HOOK_SPECS 创建 PluginRegistry
    registry = PluginRegistry(specs=HOOK_SPECS)

    # 3. 通过工厂 lambda 注册所有 13 个插件
    plugin_factories = {
        "llm_provider":      lambda: LLMProviderPlugin(...),
        "storage":           lambda: StoragePlugin(...),
        "core_tools":        lambda: CoreToolsPlugin(...),
        "approval":          lambda: ApprovalPlugin(...),
        "summarizer":        lambda: SummarizerPlugin(...),
        "memory":            lambda: MemoryPlugin(),
        "shell_session":     lambda: shell_session,
        "doom_detector":     lambda: DoomDetectorPlugin(...),
        "parallel_executor": lambda: ParallelExecutorPlugin(...),
        "topic":             lambda: TopicPlugin(...),
        "session_metrics":   lambda: SessionMetricsPlugin(),
        "skills":            lambda: SkillsPlugin(...),
        "mcp":               lambda: MCPPlugin(...),
    }

    # 4. 根据配置选择性加载插件
    enabled_plugins = cfg.plugins or list(plugin_factories.keys())
    for name in enabled_plugins:
        registry.register(plugin_factories[name]())

    # 5. 构建 runtime、pipeline、context
    runtime  = HookRuntime(registry, specs=HOOK_SPECS)
    pipeline = Pipeline(runtime=runtime, registry=registry, ...)
    ctx      = PipelineContext(tape=Tape(), session_id=..., config={...})

    return pipeline, ctx
```

### REPL 启动：`InteractiveSession`

```
run_repl(config)
    │
    ▼
InteractiveSession(config)
    ├── InputHandler()        # prompt_toolkit 会话
    ├── BashExecutor()        # Shell 模式使用
    ├── StreamingRenderer()   # Rich 控制台输出
    ├── RichConsumer()        # Wire → 渲染器桥接
    └── _setup_agent()        # 创建 Pipeline + PipelineAdapter
           │
           ▼
        session.run()         # REPL 循环：读取 → 分发 → 渲染
```

---

## 5. 适配器层

### PipelineAdapter（`adapter.py`）

适配器将 agentkit 基于事件的流水线与 Wire 协议桥接起来，是 agentkit 运行时与 UI 层之间**唯一**的接触点。

```
┌──────────────┐     流水线事件              ┌──────────────────┐
│   agentkit   │  ──────────────────────→   │ PipelineAdapter  │
│   Pipeline   │  TextEvent, ToolCallEvent  │                  │
│              │  ToolResultEvent, Done     │  _handle_event() │
└──────────────┘                           └────────┬─────────┘
                                                    │
                                           Wire 消息
                                           StreamDelta, ToolCallDelta
                                           ToolResultDelta, TurnEnd
                                                    │
                                                    ▼
                                           ┌──────────────────┐
                                           │  WireConsumer     │
                                           │  (Rich / Headless │
                                           │   / HTTP)         │
                                           └──────────────────┘
```

**核心职责：**

| 方法 | 用途 |
|---|---|
| `run_turn(user_input)` | 将用户条目追加到 tape，运行流水线，返回 `TurnOutcome` |
| `_handle_event(event)` | 将 agentkit 事件转换为 Wire 消息，调用 `consumer.emit()` |
| `_determine_stop_reason()` | 检查 tape 和插件状态，对终止原因分类 |
| `_finish(stop_reason)` | 发出 `TurnEnd`，组装 `TurnOutcome` |

### TurnOutcome（`adapter_types.py`）

```python
class StopReason(Enum):
    NO_TOOL_CALLS     = "no_tool_calls"      # Agent 响应时没有调用工具
    MAX_STEPS_REACHED = "max_steps_reached"  # 达到轮次上限
    DOOM_LOOP         = "doom_loop"          # 检测到重复工具调用
    ERROR             = "error"              # 执行过程中发生异常
    INTERRUPTED       = "interrupted"        # KeyboardInterrupt

@dataclass
class TurnOutcome:
    stop_reason: StopReason
    final_message: str | None    # 最后一条助手消息
    steps_taken: int             # 本轮工具调用次数
    error: str | None            # 错误详情（如适用）
```

---

## 6. Wire 协议

### 消息类型（`wire/protocol.py`）

所有消息均继承自 `WireMessage`（含 `session_id` 和 `timestamp`）：

| 类型 | 方向 | 用途 |
|---|---|---|
| `StreamDelta` | Agent → UI | LLM 输出的流式文本块 |
| `ToolCallDelta` | Agent → UI | 工具调用（含名称和参数） |
| `ToolResultDelta` | Agent → UI | 工具执行结果（成功/错误） |
| `ApprovalRequest` | Agent → UI | 请求用户对工具的授权 |
| `ApprovalResponse` | UI → Agent | 用户的授权决定 |
| `TurnEnd` | Agent → UI | 轮次已完成（含状态） |

### CompletionStatus

```python
class CompletionStatus(str, Enum):
    COMPLETED = "completed"   # 正常完成（无更多工具调用）
    BLOCKED   = "blocked"     # 达到最大步数或 Doom 循环
    ERROR     = "error"       # 发生异常
```

### 向后兼容

`ApprovalRequest` 和 `ApprovalResponse` 通过 `__post_init__` 同步支持双格式字段——可同时使用新协议格式（`request_id`、`tool_call`、`approved`）和旧格式（`call_id`、`tool`、`args`、`decision`）。

### LocalWire（`wire/local.py`）

用于 CLI 会话的进程内异步队列 Wire：

```python
class LocalWire:
    _outgoing: asyncio.Queue[WireMessage]   # Agent → UI
    _incoming: asyncio.Queue[WireMessage]   # UI → Agent

    async def send(message) → None          # Agent 向 UI 发送
    async def receive() → WireMessage       # Agent 从 UI 读取
    async def request_approval(tool_call, timeout) → ApprovalResponse
```

`request_approval` 流程：
1. Agent 向 `_outgoing` 发送 `ApprovalRequest`
2. UI 从 `_outgoing` 消费，显示提示
3. UI 将 `ApprovalResponse` 放入 `_incoming`
4. Agent 从 `_incoming` 读取 `ApprovalResponse`
5. 超时 → 自动拒绝并附带反馈信息

---

## 7. 插件系统

### 插件注册

所有 13 个插件均实现 agentkit 的 `Plugin` 协议：一个返回 `dict[str, Callable]` 的 `hooks()` 方法，以及一个 `state_key` 类属性。

```python
class SomePlugin:
    state_key = "some_plugin"

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {"hook_name": self.handler_method}
```

插件通过工厂 lambda 在 `create_agent()` 中注册，支持延迟构造和配置驱动的选择。

### 插件目录

| 插件 | state_key | 钩子 | 用途 |
|---|---|---|---|
| **LLMProviderPlugin** | `llm_provider` | `provide_llm` | Provider 实例工厂（Anthropic、OpenAI、Copilot、Kimi） |
| **CoreToolsPlugin** | `core_tools` | `get_tools`、`execute_tool` | 注册并执行所有文件/Shell/规划工具 |
| **ApprovalPlugin** | `approval` | `approve_tool_call` | 返回 `Approve`/`Reject`/`AskUser` 指令 |
| **StoragePlugin** | `storage` | `provide_storage`、`mount` | JSONL 支持的 `ForkTapeStore` + `FileSessionStore` |
| **SummarizerPlugin** | `summarizer` | `resolve_context_window` | 通过主题边界或条目数截断进行上下文压缩 |
| **MemoryPlugin** | `memory` | `build_context`、`on_turn_end`、`on_checkpoint`、`mount` | Grounding 注入 + 记忆提取 |
| **SkillsPlugin** | `skills` | `build_context`、`get_tools`、`execute_tool`、`on_checkpoint`、`on_session_event`、`mount` | 发现 `.agents/skills`，注入技能摘要，并激活技能 |
| **MCPPlugin** | `mcp` | `mount`、`get_tools`、`execute_tool`、`on_checkpoint` | 启动 MCP 服务并将其工具重新暴露给 Agent |
| **DoomDetectorPlugin** | `doom_detector` | `on_checkpoint` | 检测 N 次连续相同工具调用 |
| **ParallelExecutorPlugin** | `parallel_executor` | `execute_tools_batch` | 依赖感知的并行工具执行 |
| **TopicPlugin** | `topic` | `on_checkpoint`、`on_session_event`、`mount` | 基于文件重叠的主题边界检测 |
| **SessionMetricsPlugin** | `session_metrics` | `on_checkpoint`、`on_session_event` | 按轮次和主题统计性能指标 |
| **ShellSessionPlugin** | `shell_session` | `mount`、`on_checkpoint` | 跨工具调用的持久化 CWD + 环境变量追踪 |

### 插件交互图

```
用户输入
    │
    ▼
Pipeline.run_turn()
    │
    ├── provide_llm ──────────→ LLMProviderPlugin
    ├── get_tools ────────────→ CoreToolsPlugin
    ├── build_context ────────→ MemoryPlugin（注入记忆）
    ├── resolve_context_window → SummarizerPlugin（压缩 tape）
    │
    ├── [LLM 流式阶段]
    │       │
    │       ├── 检测到工具调用
    │       │       │
    │       │       ├── approve_tool_call → ApprovalPlugin
    │       │       │       ├── Approve → 执行
    │       │       │       ├── AskUser → Wire → UI → 响应
    │       │       │       └── Reject → 跳过
    │       │       │
    │       │       ├── execute_tool ──────→ CoreToolsPlugin
    │       │       │       └── bash_run → ShellSessionPlugin（同步 CWD）
    │       │       │
    │       │       └── execute_tools_batch → ParallelExecutorPlugin
    │       │
    │       └── on_checkpoint ──→ DoomDetectorPlugin
    │                           → TopicPlugin
    │                           → SessionMetricsPlugin
    │                           → MemoryPlugin
    │                           → ShellSessionPlugin
    │
    └── on_turn_end ──────────→ MemoryPlugin（提取记忆）
```

---

## 8. Provider 层

### 架构

```
┌───────────────────────────────────┐
│     agentkit LLMProvider          │  协议（结构化子类型）
│     protocol.py                   │  stream(), model_name, models()
└──────────────┬────────────────────┘
               │ 实现
    ┌──────────┼──────────────────┐
    │          │                  │
    ▼          ▼                  ▼
┌────────┐ ┌──────────────┐ ┌──────────┐
│Anthropic│ │OpenAI-Compat │ │  Copilot │
│Provider │ │  Provider    │ │ Provider │
└────────┘ └──────────────┘ └──┬───────┘
                                │ 继承
                          OpenAI-Compat
```

### Provider 详情

| Provider | 模块 | 后端 | Base URL |
|---|---|---|---|
| **AnthropicProvider** | `anthropic.py` | Anthropic Messages API | `https://api.anthropic.com` |
| **OpenAICompatProvider** | `openai_compat.py` | OpenAI Chat Completions | 可配置 |
| **CopilotProvider** | `copilot.py` | GitHub Models API | `https://models.github.ai/inference` |

### 双协议桥接

代码库中存在两套流式事件类型系统：

1. **遗留版**（`providers/base.py`）：`StreamEvent`，`type: Literal["delta", "tool_call", "done", "error"]`
2. **AgentKit 版**（`agentkit/providers/models.py`）：`TextEvent`、`ToolCallEvent`、`DoneEvent`、`ToolResultEvent`

`plugins/llm_provider.py` 中的 `adapt_stream_events()` 函数负责桥接：

```python
async def adapt_stream_events(old_stream) -> AsyncIterator[NewStreamEvent]:
    async for event in old_stream:
        if event.type == "delta":       yield TextEvent(text=event.text)
        elif event.type == "tool_call": yield ToolCallEvent(...)
        elif event.type == "done":      yield DoneEvent()
        elif event.type == "error":     yield DoneEvent()  # 降级处理
```

### Provider 工厂（LLMProviderPlugin）

`LLMProviderPlugin.provide_llm()` 方法是一个懒加载工厂：

```python
def provide_llm(self, **kwargs) -> LLMProvider:
    match self._provider_name:
        case "anthropic":               → AnthropicProvider
        case "openai"|"openai_compat":  → OpenAICompatProvider
        case "copilot":                 → CopilotProvider
        case "kimi":                    → OpenAICompatProvider(base_url=moonshot)
        case "kimi-code":               → OpenAICompatProvider(base_url=kimi, UA=claude-code)
        case "kimi-code-anthropic":     → AnthropicProvider(base_url=kimi, UA=claude-code)
```

Provider 在第一次调用时实例化，并通过 `self._instance` 缓存。

---

## 9. CLI 层

### Click CLI（`__main__.py`）

```
coding_agent CLI
├── （默认）   → REPL 模式
├── repl       → REPL 模式（显式）
├── run        → 批处理模式（需要 --goal）
│   ├── --tui  → Rich TUI 展示
│   └── （默认）→ 无头模式
├── stats      → 会话统计
└── serve      → HTTP API Server（uvicorn）
```

### REPL 循环（`cli/repl.py`）

`InteractiveSession` 管理主读取-求值-打印循环：

```
while not should_exit:
    │
    ├── user_input = await input_handler.get_input()
    │
    ├── if shell_mode:
    │       bash_executor.execute(user_input)
    │
    ├── if starts_with "/":
    │       handle_command(user_input, context)
    │
    └── else:
            renderer.user_message(input)
            adapter.run_turn(input)
```

### 输入处理器（`cli/input_handler.py`）

两个共享按键绑定的 `PromptSession` 实例：

| 模式 | Session | 多行 | 提示符 |
|---|---|---|---|
| **Chat** | `chat_session` | 是（Shift+Enter 换行） | `[0] > ` |
| **Shell** | `shell_session` | 否 | `bash dir $ ` |

**按键绑定：**

| 按键 | Chat 模式 | Shell 模式 |
|---|---|---|
| `Enter` | 提交消息 | 提交命令 |
| `Escape + Ctrl-J`（Shift+Enter） | 插入换行 | — |
| 空缓冲区输入 `!` | 切换到 Shell | — |
| 空缓冲区按 `Escape` | — | 切换到 Chat |
| 空缓冲区按 `Backspace` | — | 切换到 Chat |
| `Ctrl-C`（×1） | 清除 + 提示 | 清除 + 提示 |
| `Ctrl-C`（2 秒内 ×2） | 退出 | 退出 |
| `Ctrl-D` | 退出 | 退出 |

为兼容 Shift+Enter，注册了自定义 ANSI 转义序列：
```python
ANSI_SEQUENCES["\x1b[27;2;13~"] = (Keys.Escape, Keys.ControlJ)
ANSI_SEQUENCES["\x1b[13;2u"]    = (Keys.Escape, Keys.ControlJ)
```

### 斜杠命令（`cli/commands.py`）

基于装饰器的命令注册表：

| 命令 | 描述 |
|---|---|
| `/help` | 显示可用命令 |
| `/exit`、`/quit` | 退出 Agent |
| `/clear` | 清屏 |
| `/plan` | 显示当前计划（待办列表） |
| `/model [name]` | 显示或切换模型 |
| `/tools` | 列出可用工具 |
| `/skill [name|off]` | 列出或激活技能 |
| `/thinking ...` | 切换 thinking 模式与强度 |
| `/mcp [reload]` | 查看或重载 MCP 服务 |

命令通过 `@command(name, description)` 装饰器注册，由 `handle_command()` 分发。

---

## 10. UI 层

### 设计：基于滚动缓冲区的架构

UI 有意避免全屏 TUI 框架（如 Textual），而是使用 `prompt_toolkit` 处理输入，`rich` 处理输出，采用基于滚动缓冲区的设计——类似于 Claude Code / aider。

```
┌─────────────────────────────────────────┐
│           WireConsumer 协议              │  async emit(msg) + request_approval()
├──────────┬──────────────┬───────────────┤
│  Rich    │  Headless    │  HTTP Server  │
│ Consumer │  Consumer    │  Consumer     │
├──────────┤              │               │
│ 流式渲染  │  日志 +      │  SSE/WebSocket│
│ 器（Rich） │  stdout      │  + REST       │
└──────────┴──────────────┴───────────────┘
```

### WireConsumer 协议

```python
class WireConsumer(Protocol):
    async def emit(self, msg: WireMessage) -> None: ...
    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse: ...
```

所有 UI 后端均实现此协议。适配器无需知道当前激活的是哪个后端。

### StreamingRenderer（`ui/stream_renderer.py`）

核心渲染引擎，处理：

- **流式文本**：实时向终端写入原始字符，流结束后（若检测到 Markdown 语法）重新渲染为 Rich Markdown
- **工具调用面板**：带工具图标和参数预览的 Rich `Panel`
- **工具结果面板**：计时、截断（1000 字符）、错误高亮
- **清除并重渲染**：使用 ANSI 光标控制擦除已流式输出的文本，替换为格式化 Markdown

```python
class StreamingRenderer:
    stream_start()                    # 开始文本积累
    stream_text(text)                 # 原始字符输出 + 缓冲
    stream_end()                      # 如需要则重渲染为 Markdown
    tool_call(call_id, name, args)    # 渲染工具调用面板
    tool_result(call_id, name, result, is_error)  # 渲染结果面板
    turn_end(status)                  # 轮次完成指示器
```

**工具图标：**

| 匹配模式 | 图标 |
|---|---|
| `file` | 📄 |
| `grep`、`search` | 🔍 |
| `bash` | ⚡ |
| `glob` | 📂 |
| `todo` | 📋 |
| （其他） | 🔧 |

### RichConsumer（`ui/rich_consumer.py`）

通过模式匹配将 Wire 消息分发到 `StreamingRenderer`：

```python
match msg:
    case StreamDelta(content=text):     renderer.stream_text(text)
    case ToolCallDelta(tool_name=...):  renderer.tool_call(...)
    case ToolResultDelta(...):          renderer.tool_result(...)
    case TurnEnd(completion_status=s):  renderer.turn_end(s.value)
```

同时管理**会话级自动审批**：一旦某工具以 `scope="session"` 被批准，后续同名工具调用将直接跳过审批提示。

### HeadlessConsumer（`ui/headless.py`）

用于批处理/CI 模式：
- `StreamDelta` → `print(text, end="", flush=True)`（原始 stdout）
- `ToolCallDelta` → `logger.info()`
- `TurnEnd` → `logger.info()`
- 自动批准所有工具调用（可配置）

### 审批提示（`ui/approval_prompt.py`）

带工具专属预览的交互式审批：

| 工具类型 | 预览 |
|---|---|
| `bash` | 语法高亮命令面板 |
| `file_write` | 文件路径 + 语法高亮内容 |
| `file_edit` | 红/绿差异对比（旧/新文本） |
| （其他） | 通用键值对展示 |

用户选项：`[y]` 单次批准，`[a]` 全部批准（会话），`[n]` 拒绝，`[r]` 拒绝并附原因。

---

## 11. Tools 层

### 工具注册

工具在 `CoreToolsPlugin._register_tools()` 中通过 `ToolRegistry` 注册：

```python
def _register_tools(self):
    file_read, file_write, file_replace, glob_files, grep_search = build_file_tools(workspace_root)
    file_patch = build_file_patch_tool(workspace_root)
    todo_write, todo_read = build_planner_tools(planner)

    for fn in (file_read, file_write, file_replace, glob_files,
               grep_search, bash_run, todo_write, todo_read, file_patch):
        self._registry.register(fn)
```

### 工具目录

| 工具 | 模块 | 描述 |
|---|---|---|
| `file_read` | `file_ops.py` | 读取文件内容 |
| `file_write` | `file_ops.py` | 写入/创建文件 |
| `file_replace` | `file_ops.py` | 文件内搜索并替换 |
| `glob_files` | `file_ops.py` | Glob 模式文件搜索 |
| `grep_search` | `file_ops.py` | 正则内容搜索 |
| `file_patch` | `file_patch_tool.py` | 结构化文件补丁 |
| `bash_run` | `shell.py` | Shell 命令执行 |
| `todo_write` | `planner.py` | 创建/更新任务计划 |
| `todo_read` | `planner.py` | 读取当前任务计划 |

### Shell 会话同步

`bash_run` 执行时，`CoreToolsPlugin._sync_shell_session()` 检查结果以追踪：
- **目录变更**：`"Changed directory to /foo"` → `shell_session.update_cwd("/foo")`
- **环境变量导出**：`"Exported KEY=value"` → `shell_session.update_env("KEY", "value")`

这实现了跨工具调用的持久化 Shell 状态（"Kapybara 模式"）。

`bash_run` 与 REPL 的 `!` shell mode 不是一回事。`bash_run` 只执行单条命令，并显式禁止 `&&`、`||`、管道、重定向、`;` 和后台执行；而 REPL 的 `!` 模式通过 `BashExecutor` 启动真实 shell 子进程，所以这些语法在那条路径里是可用的。

### 并行执行

`ParallelExecutorPlugin` 提供依赖感知的并行工具执行：

```
输入：[file_read(a), file_read(b), file_write(a)]
                    │
    DependencyAnalyzer.can_run_in_parallel()
                    │
    批次 1：[file_read(a), file_read(b)]  ← 并行（不同文件，均为读）
    批次 2：[file_write(a)]               ← 顺序（依赖 file_read(a)）
```

**冲突规则：**
- 同一文件的读+写 → 顺序执行
- 同一文件的写+写 → 顺序执行
- 不同文件的操作 → 并行执行
- 非文件工具 → 始终并行

由 `asyncio.Semaphore(max_concurrency)` 控制（默认：5）。

### 工具结果缓存

`tools/cache.py` 为幂等工具结果提供 LRU 缓存（可通过 `--cache/--no-cache`、`--cache-size` 配置）。

---

## 12. 审批系统

### 三层设计

```
┌──────────────────────────────────┐
│  第 1 层：ApprovalPlugin          │  插件钩子：approve_tool_call()
│  返回 Approve/Reject/AskUser      │  基于策略 + 被封禁工具
├──────────────────────────────────┤
│  第 2 层：DirectiveExecutor       │  AgentKit 核心：执行指令
│  将 AskUser 路由到 _ask_user()    │  可插拔回调
├──────────────────────────────────┤
│  第 3 层：UI 审批                 │  RichConsumer.request_approval()
│  approval_prompt.py              │  → prompt_approval() 交互式 UI
│  HeadlessConsumer                │  → 自动批准或自动拒绝
└──────────────────────────────────┘
```

### 策略引擎（`approval/policy.py`）

```python
class ApprovalPolicy(Enum):
    YOLO        = "yolo"         # 自动批准所有
    INTERACTIVE = "interactive"  # 始终询问
    AUTO        = "auto"         # 仅自动批准安全工具

class PolicyEngine:
    def needs_approval(self, tool_name) -> bool:
        match self.config.policy:
            case YOLO:        return False
            case INTERACTIVE: return True
            case AUTO:        return tool_name not in self.config.safe_tools
```

默认安全工具：`{"file_read", "repo_list", "git_status"}`

### ApprovalPlugin（`plugins/approval.py`）

使用 agentkit 的指令类型：

```python
def approve_tool_call(self, tool_name, arguments, **kwargs):
    if tool_name in self._blocked_tools:
        return Reject(reason=f"工具 '{tool_name}' 已被封禁")
    match self._policy:
        case AUTO:      return Approve()
        case MANUAL:    return AskUser(question=f"允许 '{tool_name}'？")
        case SAFE_ONLY: return Approve() if tool_name in self._safe_tools else AskUser(...)
```

### 会话级审批

`RichConsumer` 追踪 `_session_approved_tools: set[str]`。当用户以 `scope="session"` 批准某工具时，后续同名工具调用将自动批准，无需再次提示。

---

## 13. 配置

### 分层优先级

```
CLI 参数  >  环境变量  >  默认值
```

### Config 模型（`core/config.py`）

```python
class Config(BaseModel):
    # Provider
    provider: Literal["openai", "anthropic", "copilot", "kimi", "kimi-code", "kimi-code-anthropic"]
    model: str = "gpt-4o"
    api_key: SecretStr | None
    base_url: str | None

    # 行为
    max_steps: int = 30
    approval_mode: Literal["yolo", "interactive", "auto"] = "yolo"
    doom_threshold: int = 3

    # 路径
    repo: Path = Path(".")
    tape_dir: Path = ~/.coding-agent/tapes

    # 子 Agent
    max_subagent_depth: int = 3
    subagent_max_steps: int = 15

    # 执行
    enable_parallel_tools: bool = True
    max_parallel_tools: int = 5

    # 缓存
    enable_cache: bool = True
    cache_size: int = 100

    # HTTP
    http_api_key: str | None
```

当前实现补充说明：

- 技能发现由 `SkillsPlugin` 负责，会扫描 `<workspace>/.agents/skills`、`~/.agents/skills`，以及 `agent.toml` 中的 `skills.extra_dirs`。
- 运行时的 tape 持久化路径当前由 `coding_agent.app.create_agent()` 与 `StoragePlugin` 根据 `data_dir` / `AGENT_DATA_DIR` 决定，因此 `Config.tape_dir` 还不是主要的运行时存储开关。

### 环境变量映射

| 环境变量 | 配置字段 |
|---|---|
| `AGENT_API_KEY` | `api_key` |
| `AGENT_MODEL` | `model` |
| `AGENT_BASE_URL` | `base_url` |
| `AGENT_PROVIDER` | `provider` |
| `AGENT_MAX_STEPS` | `max_steps` |
| `AGENT_APPROVAL_MODE` | `approval_mode` |
| `AGENT_DOOM_THRESHOLD` | `doom_threshold` |
| `AGENT_REPO` | `repo` |
| `AGENT_ENABLE_PARALLEL_TOOLS` | `enable_parallel_tools` |
| `AGENT_MAX_PARALLEL_TOOLS` | `max_parallel_tools` |
| `AGENT_HTTP_API_KEY` | `http_api_key` |

### Provider 专属密钥解析

```python
if provider == "copilot" and no api_key:
    api_key = GITHUB_TOKEN
elif provider == "kimi" and no api_key:
    api_key = MOONSHOT_API_KEY
elif provider in ("kimi-code", "kimi-code-anthropic") and no api_key:
    api_key = KIMI_CODE_API_KEY
```

---

## 14. 上下文管理

### 摘要策略

`SummarizerPlugin` 采用两级上下文窗口管理策略：

**策略 1 — 主题边界折叠：**
若 tape 含有 `topic_finalized` 锚点且超过 `max_entries`，则在最后一个主题边界处折叠。折叠产生一个摘要锚点，列出主题数量和涉及的文件。

**策略 2 — 条目数截断：**
无主题边界时的回退方案。保留最近 `keep_recent` 条条目的原文，其余压缩为一个锚点。

### 主题检测

`TopicPlugin` 通过监控轮次间文件路径的重叠来检测主题切换：

1. 从工具调用参数中提取文件路径
2. 与当前主题的文件集合比较
3. 若重叠率 < `overlap_threshold`（默认：0.2）→ 新主题
4. 插入 `topic_start` 锚点 + 触发 `on_session_event`
5. 上一个主题获得 `fold_boundary` 锚点（供摘要器使用）

### 记忆 Grounding

`MemoryPlugin` 以两种模式运行：

1. **Grounding**（`build_context` 钩子）：每次 LLM 调用前，以系统消息形式注入最相关的 top-N 记忆。在可用时按主题文件重叠过滤。

2. **提取**（`on_turn_end` 钩子）：生成一条 `MemoryRecord`，包含：
   - `summary`：助手消息的最后 200 个字符
   - `tags`：从 tape 中提取的工具名称 + 文件路径
   - `importance`：基于工具调用次数和消息数的启发式得分（0-1）

---

## 15. 依赖关系图

```
__main__.py
├── adapter.py
│   ├── agentkit（Pipeline, PipelineContext, Entry）
│   ├── agentkit（TextEvent, ToolCallEvent, ToolResultEvent, DoneEvent）
│   ├── adapter_types.py（StopReason, TurnOutcome）
│   └── wire/protocol.py（StreamDelta, ToolCallDelta, ToolResultDelta, TurnEnd）
│
├── cli/repl.py
│   ├── cli/input_handler.py（prompt_toolkit）
│   ├── cli/commands.py
│   ├── cli/bash_executor.py
│   ├── adapter.py（PipelineAdapter）
│   ├── ui/stream_renderer.py（rich）
│   └── ui/rich_consumer.py
│
├── core/config.py（pydantic）
│
├── plugins/（各插件依赖 agentkit 钩子协议）
│   ├── llm_provider.py → providers/*.py
│   ├── core_tools.py → tools/*.py + agentkit（ToolRegistry）
│   ├── approval.py → agentkit（Approve, Reject, AskUser）
│   ├── storage.py → agentkit（ForkTapeStore, FileSessionStore）
│   ├── summarizer.py → agentkit（Tape, Entry）
│   ├── memory.py → agentkit（MemoryRecord, Tape, Entry）
│   ├── doom_detector.py →（无外部依赖）
│   ├── parallel_executor.py →（仅 asyncio）
│   ├── topic.py → agentkit（Tape, Entry）
│   ├── metrics.py →（仅标准库）
│   └── shell_session.py →（仅标准库）
│
└── ui/
    ├── rich_consumer.py → wire/protocol.py
    ├── stream_renderer.py → rich
    ├── approval_prompt.py → wire/protocol.py + rich
    ├── headless.py → wire/protocol.py + logging
    └── http_server.py → fastapi + wire/protocol.py
```

---

## 16. 数据流图

### 交互式 REPL：完整请求生命周期

```
用户输入 "fix the bug in main.py"
    │
    ▼
InputHandler.get_input()
    │（prompt_toolkit）
    ▼
InteractiveSession._process_message("fix the bug in main.py")
    │
    ├── renderer.user_message("fix the bug...")     ← UI：显示用户输入
    │
    └── adapter.run_turn("fix the bug...")
            │
            ├── tape.append(Entry(kind="message", role="user", ...))
            │
            └── pipeline.run_turn(ctx)
                    │
                    ├── [resolve_session]
                    ├── [load_state]       ← StoragePlugin 加载 tape
                    ├── [build_context]    ← MemoryPlugin 注入记忆
                    │                      ← SummarizerPlugin 压缩
                    │
                    ├── [run_model]        ← LLMProviderPlugin → LLM API
                    │       │
                    │       ├── TextEvent("我来读取这个文件...")
                    │       │       │
                    │       │       └── adapter._handle_event()
                    │       │               └── consumer.emit(StreamDelta(...))
                    │       │                       └── renderer.stream_text(...)
                    │       │
                    │       ├── ToolCallEvent("file_read", {path: "main.py"})
                    │       │       │
                    │       │       └── adapter._handle_event()
                    │       │               └── consumer.emit(ToolCallDelta(...))
                    │       │                       └── renderer.tool_call(...)
                    │       │
                    │       └── DoneEvent()
                    │
                    ├── [execute_tools]
                    │       ├── approve_tool_call → ApprovalPlugin → Approve
                    │       ├── execute_tool → CoreToolsPlugin → file_read("main.py")
                    │       └── ToolResultEvent → adapter → ToolResultDelta → renderer
                    │
                    ├── [on_checkpoint]
                    │       ├── DoomDetectorPlugin：检查循环
                    │       ├── TopicPlugin：检测主题切换
                    │       ├── SessionMetricsPlugin：更新计数器
                    │       └── MemoryPlugin：缓存文件标签
                    │
                    ├── [save_state]       ← StoragePlugin 持久化 tape
                    │
                    └── [dispatch]         ← 如有更多工具调用则重复
                            │
                            └──（循环回 run_model，直到无工具调用为止）
                                    │
                                    └── 最终 TextEvent("修复方案如下...")
                                            │
                                            └── TurnEnd(COMPLETED)
```

### 批处理模式：无头流程

```
_run_headless(config, goal)
    │
    ├── create_agent() → (pipeline, ctx)
    ├── HeadlessConsumer(auto_approve=True)
    ├── PipelineAdapter(pipeline, ctx, consumer)
    │
    └── adapter.run_turn(goal)
            │
            ├── StreamDelta → print(text, end="")    ← 原始 stdout
            ├── ToolCallDelta → logger.info(...)
            ├── ApprovalRequest → 自动批准
            └── TurnEnd → click.echo("--- 结果 ---")
```

---

## 17. 错误处理

### Doom 循环检测

`DoomDetectorPlugin` 对工具调用（名称 + 参数）进行哈希，追踪连续相同调用。当连续相同调用达到 `threshold`（默认：3）次时：

1. 设置 `ctx.plugin_states["doom_detector"]["doom_detected"] = True`
2. `PipelineAdapter._determine_stop_reason()` 返回 `StopReason.DOOM_LOOP`
3. 轮次以 `CompletionStatus.BLOCKED` 结束

### 轮次终止

| 条件 | StopReason | CompletionStatus |
|---|---|---|
| Agent 响应时没有工具调用 | `NO_TOOL_CALLS` | `COMPLETED` |
| 达到工具调用上限 | `MAX_STEPS_REACHED` | `BLOCKED` |
| 检测到 Doom 循环 | `DOOM_LOOP` | `BLOCKED` |
| Python 异常 | `ERROR` | `ERROR` |
| KeyboardInterrupt | `INTERRUPTED` | `ERROR` |

### 优雅恢复

REPL 将 `_process_message()` 包裹在 try/except 中——错误会被显示，但会话仍可继续：

```python
try:
    await self._process_message(user_input)
except Exception as e:
    print_pt(f"\nAgent 执行过程中出错：{e}")
    print_pt("您可以继续发送新消息。\n")
```
