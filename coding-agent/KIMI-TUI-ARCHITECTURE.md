# Kimi-CLI TUI 架构分析

基于 kimi-cli 源码的 TUI（终端用户界面）设计总结。

## 技术栈概览

| 层级 | 技术 |
|------|------|
| 语言 | Python 3.12+（async/await 原生支持） |
| 输入框架 | **Prompt Toolkit** v3.0.52 — 交互式提示、键绑定、全屏布局 |
| 输出框架 | **Rich** v14.2.0 — 终端格式化、面板、表格、Markdown 渲染 |
| 状态管理 | Shell 实例属性 + 异步事件路由 |
| 主题系统 | 自定义 `theme.py`（dark/light 全局切换） |

## 目录结构

```
src/kimi_cli/
├── ui/
│   ├── shell/                   # 交互式 Shell 与提示
│   │   ├── __init__.py          # Shell 主循环（991 行）
│   │   ├── prompt.py            # CustomPromptSession（2124 行）
│   │   ├── visualize.py         # 实时流式输出渲染（1497 行）
│   │   ├── approval_panel.py    # 审批模态面板（481 行）
│   │   ├── question_panel.py    # 问题输入面板
│   │   ├── task_browser.py      # 后台任务管理全屏 TUI（486 行）
│   │   ├── keyboard.py          # 跨平台键盘监听（300 行）
│   │   ├── placeholders.py      # 附件占位符解析（530 行）
│   │   └── console.py           # 自定义 Rich Console
│   ├── print/                   # 非交互式输出（Web UI/JSON）
│   ├── acp/                     # Agent Client Protocol UI
│   └── theme.py                 # 集中式主题管理（239 行）
├── utils/rich/                  # Rich 库扩展
│   ├── markdown.py              # 自定义 Markdown 渲染（900+ 行）
│   ├── syntax.py                # 代码语法高亮（115 行）
│   ├── diff_render.py           # 统一 diff 可视化（400+ 行）
│   └── columns.py               # 布局工具
└── app.py                       # KimiCLI 应用入口
```

UI 相关代码合计约 **9,340+ 行**（仅 shell 模块）。

---

## 1. 整体架构

```
┌─────────────────────────────────────────┐
│         Shell（交互主循环）               │
│   ui/shell/__init__.py                  │
└──────────────┬──────────────────────────┘
               │
     ┌─────────┼──────────┬───────────────┐
     │         │          │               │
┌────▼────┐ ┌──▼───────┐ ┌▼──────────┐ ┌──▼──────────┐
│Prompt   │ │Visualizer│ │Approval   │ │Task Browser │
│Session  │ │(流式渲染) │ │Panel      │ │(全屏 TUI)   │
│(2124行) │ │(1497行)  │ │(481行)    │ │(486行)      │
└────┬────┘ └────┬─────┘ └─────┬─────┘ └─────────────┘
     │           │             │
     └───────────┴─────────────┘
                 │
        ┌────────▼──────────┐
        │   Rich Console    │
        │ (Markdown/Code/   │
        │  Diff/Panel)      │
        └────────┬──────────┘
                 │
        ┌────────▼──────────┐
        │ Terminal (ANSI)   │
        └───────────────────┘
```

**职责分离：** Prompt Toolkit 负责输入与布局，Rich 负责输出渲染。两者通过 Shell 协调。

---

## 2. Shell 主循环

**文件：** `ui/shell/__init__.py`（991 行）

Shell 是 TUI 的中央调度器，管理用户输入、Agent 执行和输出可视化的生命周期。

### 状态

```python
class Shell:
    soul: Soul                                          # Agent 执行引擎
    _prompt_session: CustomPromptSession                # 交互提示
    _running_input_handler: Callable[[UserInput], None] # Agent 执行期间的实时输入
    _running_interrupt_handler: Callable[[], None]      # 中断处理
    _pending_approval_requests: deque[ApprovalRequest]  # 待审批队列
    _approval_modal: ApprovalPromptDelegate             # 审批模态
    _exit_after_run: bool                               # 单次执行模式
```

### 异步事件路由

```python
async def _route_prompt_events():
    """异步路由器 — 分类处理所有输入事件"""
    event = await prompt_session.prompt_next()
    match event.kind:
        case "input":     # 正常文本 → run_soul_command()
        case "interrupt": # Ctrl-C
        case "eof":       # Ctrl-D → 退出
        case "error":     # 提示会话崩溃
        case "bg_noop":   # 后台任务完成通知
```

### 输入分类

```
用户输入
    ↓
分类判断：
  ├─ Ctrl-X → Shell 命令模式切换
  ├─ /xxx   → Slash 命令（补全 + 执行）
  ├─ 文本    → Agent 执行（run_soul_command）
  └─ exit   → 清理并退出
```

---

## 3. CustomPromptSession — 交互提示

**文件：** `ui/shell/prompt.py`（2124 行）

基于 Prompt Toolkit 的交互式提示，是用户与 TUI 交互的主要入口。

### 双模式提示

| 模式 | 触发 | 用途 |
|------|------|------|
| `PromptMode.AGENT` | 默认 | AI 对话 + Slash 命令 |
| `PromptMode.SHELL` | Ctrl-X | Shell 命令执行 |

### 核心功能

**动态底部工具栏：**
- 提示符号、当前工作目录、后台任务数、提示信息
- 根据 `StatusSnapshot` 实时更新
- 颜色跟随主题

**Slash 命令补全：**
- `SlashCommandCompleter` — 模糊匹配 `/` 命令
- `SlashCommandMenuControl` — 全宽补全菜单，显示描述与元信息

**实时转向输入（Running Prompt）：**
- Agent 执行期间，提示保持活跃，接受 "steer" 输入
- 通过 `attach_running_prompt(delegate)` / `detach_running_prompt(delegate)` 绑定
- 独立的运行态/空闲态键绑定

**附件处理：**
- `@file:path` — 内联文件内容
- `@image:path` — 图片预览
- 剪贴板粘贴媒体提取
- 代理字符归一化

---

## 4. Visualizer — 流式输出渲染

**文件：** `ui/shell/visualize.py`（1497 行）

实时渲染 Agent 流式输出，核心设计是 **增量 Markdown 提交（Incremental Markdown Commitment）**。

### 增量 Markdown 提交

```
原始流式文本
    ↓
markdown-it 解析 Markdown 块
    ↓
识别"已提交"块（完整、非叶子节点）
    ↓
已提交块 → 永久打印到终端（不再更新）
    ↓
未确认尾部 → 保留在 Rich Live 中（瞬态，持续更新）
```

**优势：** 避免流式 Markdown 渲染的闪烁问题。完整的块立即固定，仅更新未完成的尾部。

### Wire 消息处理

```python
async def visualize_loop(wire):
    while True:
        msg = await wire.receive()
        match msg:
            case ContentPart():      # AI 文本响应 → 增量提交
            case ToolCall():         # 工具调用 → 显示调用信息
            case ToolResult():       # 工具结果 → 显示输出
            case ApprovalRequest():  # 审批请求 → 模态面板
            case StatusUpdate():     # Token 计数/计时
```

### 关键类

| 类 | 职责 |
|----|------|
| `_ContentBlock` | 管理流式内容的增量提交，追踪 Token 估算（CJK/ASCII 感知） |
| `_LiveView` | 核心可视化循环，消费 Wire 消息，渲染实时输出 |
| `_PromptLiveView` | 扩展 `_LiveView`，集成运行态提示，管理可见性 |

### 输出渲染

- Rich `Live` 上下文管理瞬态更新
- Panel/Table 面板展示工具调用
- Spinner 动画（思考/工具执行中）
- 自定义 Markdown 渲染 + 语法高亮

---

## 5. 输入处理

### 键盘监听（`ui/shell/keyboard.py`，300 行）

跨平台异步键盘监听器：

```python
class KeyboardListener:
    async start()  → None      # 启动后台线程
    async pause()  → None      # 暂停（模态期间）
    async resume() → None      # 恢复
    async get()    → KeyEvent  # 获取下一个按键事件
```

| 平台 | 实现 |
|------|------|
| Unix | `termios` 原始模式 + `sys.stdin.buffer.read()` |
| Windows | `msvcrt.getch()` + 特殊键处理 |

运行在独立守护线程中，通过线程安全队列通信。

### 支持的按键

- 方向键（上/下/左/右）
- Enter, Escape, Tab, Space
- Ctrl+X — Agent/Shell 模式切换
- Ctrl+E — 审批面板展开
- 数字键 1-6 — 审批选项选择
- Prompt Toolkit 标准编辑键（Emacs 风格）

---

## 6. 审批与问答面板

### ApprovalRequestPanel（`approval_panel.py`，481 行）

Agent 操作审批的交互式模态面板：

```
┌─ Approval Request ──────────────────────┐
│ [预览: diff/命令/文本，限制 4 行]         │
├─────────────────────────────────────────┤
│ [1] 批准一次                             │
│ [2] 本会话内批准                          │
│ [3] 拒绝                                │
│ [4] 拒绝并告知 Agent 原因                 │
└─────────────────────────────────────────┘
```

- Diff 预览 + 语法高亮
- Shell 命令预览
- 键盘导航（方向键 + 数字键）
- 选项 4 的内联反馈输入

### 审批桥接（跨异步边界）

```
Agent 发送 ApprovalRequest
    ↓ [Wire 通道]
Visualizer 接收，创建模态
    ↓ [用户交互]
ApprovalResponse
    ↓ [Wire 通道回传]
Agent 继续执行
```

---

## 7. 任务浏览器

**文件：** `ui/shell/task_browser.py`（486 行）

基于 Prompt Toolkit `Application` 的全屏 TUI，管理后台任务：

```
┌────────────────────────────────────────┐
│ [Header] Running: 2  Failed: 1        │
├────────────────────────────────────────┤
│ > [running] 任务描述 (id1)             │
│   [failed]  另一个任务 (id2)           │
│   [success] 已完成任务 (id3)           │
├────────────────────────────────────────┤
│ [详情面板：任务元数据]                   │
├────────────────────────────────────────┤
│ [输出预览：最后 6 行]                   │
├────────────────────────────────────────┤
│ Enter: 展开  S: 停止  R: 刷新          │
└────────────────────────────────────────┘
```

使用 `RadioList` 组件 + `HSplit`/`VSplit` 布局。

---

## 8. 主题系统

**文件：** `ui/theme.py`（239 行）

全局主题管理，支持 dark/light 切换：

```python
ThemeName = Literal["dark", "light"]
_active_theme: ThemeName = "dark"

# 公共 API
set_active_theme(theme: ThemeName)
get_active_theme() -> ThemeName
get_diff_colors() -> DiffColors
get_task_browser_style() -> PTKStyle
get_prompt_style() -> PTKStyle
get_toolbar_colors() -> ToolbarColors
get_mcp_prompt_colors() -> MCPPromptColors
```

### 颜色定义

```python
@dataclass(frozen=True)
class DiffColors:
    add_bg, del_bg, add_hl, del_hl: RichStyle

@dataclass(frozen=True)
class ToolbarColors:
    separator, yolo_label, plan_label, cwd, bg_tasks, tip: str
```

| 主题 | 背景色 | 特点 |
|------|--------|------|
| Dark | `#0f172a`, `#111827` | 亮色文本，高对比度 |
| Light | `#e5e7eb`, `#f9fafb` | 暗色文本，柔和背景 |

---

## 9. Rich 扩展

### 自定义 Markdown 渲染（`utils/rich/markdown.py`，900+ 行）

基于 Rich 的 Markdown 渲染器扩展：

```
Markdown 文本
    ↓
markdown-it 解析器
    ↓
MarkdownElement 树
    ↓
Pygments 语法高亮
    ↓
Rich Renderables（Text, Table 等）
    ↓
Terminal ANSI 输出
```

支持：代码块语法高亮、删除线、表格、引用块。

### 统一 Diff 渲染（`utils/rich/diff_render.py`，400+ 行）

完整的 diff 可视化管线：

1. **解析统一 diff** → DiffLine 对象（ADD/DELETE/CONTEXT）
2. **语法高亮**每行（按文件扩展名检测语言）
3. **内联 diff** — 对配对的 -/+ 行，使用 `SequenceMatcher` 做词级差异高亮（相似度 > 0.5 时）
4. **渲染为 Rich Table** — 三列（行号、标记、代码），增/删行带背景色

### 代码语法高亮（`utils/rich/syntax.py`，115 行）

```python
KIMI_ANSI_THEME = ANSISyntaxTheme({
    Keyword:       Style(color="magenta"),
    Name.Function: Style(color="bright_cyan"),
    String:        Style(color="bright_blue"),
    Comment:       Style(color="bright_black", italic=True),
})
```

---

## 10. 完整渲染管线

```
用户输入
    │
    ├─→ [CustomPromptSession]
    │   ├─ 语法高亮
    │   ├─ 补全菜单
    │   ├─ 底部状态栏
    │   └─→ UserInput 对象
    │
    └─→ [Agent 执行] (Soul.run)
        │
        ├─→ [Wire] 通信通道
        │   └─→ WireMessage（流式）
        │
        ├─→ [Visualizer]
        │   ├─ 增量 Markdown 提交
        │   │   ├─ 已完成块 → 永久打印
        │   │   └─ 未完成尾部 → Live 瞬态更新
        │   ├─ 内容渲染
        │   │   ├─ Markdown（自定义渲染器）
        │   │   ├─ 代码块（KimiSyntax）
        │   │   ├─ 工具调用（格式化面板）
        │   │   └─ Diff（diff_render）
        │   ├─ 审批模态（按需）
        │   └─ Rich Live 上下文
        │
        └─→ Terminal ANSI 输出
            ├─ 颜色/样式/格式化
            └─ OSC 8 可点击链接
```

---

## 11. 关键设计决策

1. **Prompt Toolkit + Rich 分工** — PTK 管输入与布局，Rich 管输出渲染，职责清晰
2. **增量 Markdown 提交** — 解决流式 Markdown 渲染闪烁问题，完整块固定输出，仅更新尾部
3. **双模式提示** — Agent 执行期间提示保持活跃，支持实时转向（steer）输入
4. **异步路由器模式** — Shell 的 `_route_prompt_events()` 统一处理用户输入、后台任务、中断
5. **审批桥接** — 跨异步边界的双向审批流，Wire 通道连接 Agent 与 UI
6. **全局主题** — 单一 `_active_theme` 变量，无需通过调用栈传递
7. **模态覆盖模式** — 审批/问答面板作为可拆卸的 delegate
8. **OSC 8 兼容处理** — Rich 的超链接通过零宽转义包装，兼容 Prompt Toolkit 的 ANSI 解析器
9. **跨平台键盘** — Unix（termios）/ Windows（msvcrt）分别实现，守护线程 + 线程安全队列
10. **Token 估算** — CJK/ASCII 感知的启发式浮点累加，用于流式进度指示

---

## 12. 与 Claude Code TUI 的对比

| 维度 | Claude Code | Kimi-CLI |
|------|-------------|----------|
| 语言 | TypeScript/TSX | Python 3.12+ |
| UI 框架 | 自定义 Ink（React reconciler） | Prompt Toolkit + Rich |
| 渲染方式 | 自定义帧差分 + 双缓冲 | Rich Live + 增量 Markdown 提交 |
| 布局引擎 | Yoga Flexbox | Prompt Toolkit HSplit/VSplit |
| 状态管理 | Zustand | Shell 实例属性 |
| Vim 支持 | 完整状态机（操作符/动作/文本对象） | 无 |
| 输入处理 | 自定义解析器（Kitty/xterm 协议） | Prompt Toolkit + 自定义 KeyboardListener |
| 滚动 | 自定义 ScrollBox + 硬件滚动（DECSTBM） | Rich Live 瞬态更新 |
| 文本选择 | 完整鼠标选择 + copy-on-select | 终端原生选择 |
| 复杂度 | 极高（自定义渲染器层） | 中等（利用成熟库） |
| 设计哲学 | 从底层构建完全控制 | 组合成熟库快速实现 |
