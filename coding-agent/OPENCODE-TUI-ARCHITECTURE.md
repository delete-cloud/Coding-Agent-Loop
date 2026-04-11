# OpenCode TUI 架构分析

基于 OpenCode 源码的 TUI（终端用户界面）设计总结。

## 技术栈概览

| 层级 | 技术 |
|------|------|
| 语言 | TypeScript（Bun 运行时） |
| UI 框架 | **OpenTUI** 0.1.95 — 基于 Solid.js 的终端 UI 框架 |
| 渲染引擎 | `@opentui/core`（RGBA 颜色、TextAttributes、Renderables） |
| 响应式层 | `@opentui/solid`（Solid.js 集成，JSX 终端组件） |
| 状态管理 | **Solid.js** 信号（signals）+ 存储（stores） |
| 后端通信 | `@opencode-ai/sdk`（SSE 事件流） |
| 插件系统 | `@opencode-ai/plugin`（可扩展插槽） |

TUI 代码总量约 **14,600+ 行**，100+ 文件。

## 目录结构

```
packages/opencode/src/cli/cmd/tui/
├── app.tsx                     # 主入口（TUI 函数 + Provider 树）
├── attach.ts                   # 多会话附加逻辑
├── thread.ts                   # 会话/线程管理
├── event.ts                    # 事件总线类型定义
├── win32.ts                    # Windows 终端特殊处理
├── worker.ts                   # Worker 进程管理
├── component/                  # 可复用 UI 组件
│   ├── dialog-*.tsx           # 对话框组件（15+ 种）
│   ├── prompt/                # 输入/提示系统
│   ├── spinner.tsx            # 加载指示器
│   ├── border.tsx             # 边框渲染
│   └── todo-item.tsx          # 任务渲染
├── context/                    # Solid.js 上下文（状态 Provider）
│   ├── route.tsx              # 导航路由
│   ├── sdk.tsx                # 后端 SDK 连接
│   ├── sync.tsx               # 数据同步存储（核心状态）
│   ├── theme.tsx              # 主题系统（30+ 主题）
│   ├── keybind.tsx            # 快捷键管理
│   ├── local.tsx              # 本地 Agent/模型状态
│   ├── kv.tsx                 # 键值持久化存储
│   ├── prompt.tsx             # 提示输入状态
│   ├── exit.tsx               # 退出/清理处理
│   ├── tui-config.tsx         # TUI 配置
│   ├── helper.tsx             # 上下文工具函数
│   └── theme/                 # 主题 JSON 文件（30+ 个）
├── routes/                     # 路由/页面组件
│   ├── home.tsx               # 首页（会话创建）
│   └── session/               # 会话视图
│       ├── index.tsx          # 主会话显示
│       ├── sidebar.tsx        # 侧边栏
│       ├── dialog-*.tsx       # 会话专属对话框
│       └── footer.tsx         # 会话页脚
├── feature-plugins/           # 内置功能插件
│   ├── sidebar/               # 侧边栏扩展
│   ├── home/                  # 首页扩展
│   └── system/                # 系统插件
├── plugin/                     # 插件运行时
│   ├── runtime.ts             # 插件生命周期管理
│   ├── api.tsx                # 插件 API 表面
│   ├── slots.tsx              # 插件插槽系统
│   └── internal.ts            # 内置插件
├── ui/                        # 核心 UI 原语
│   ├── dialog.tsx             # 对话框框架
│   ├── dialog-select.tsx      # 选择对话框
│   ├── toast.tsx              # Toast 通知
│   └── spinner.ts             # Spinner 动画
└── util/                      # 工具函数
    ├── terminal.ts            # 终端颜色检测
    ├── clipboard.ts           # 剪贴板 I/O
    ├── editor.ts              # 外部编辑器集成
    ├── transcript.ts          # 会话记录格式化
    └── selection.ts           # 文本选择
```

---

## 1. 整体架构

```
┌───────────────────────────────────────────────────┐
│              App（Provider 树根）                    │
│   ErrorBoundary → 19 层嵌套 Provider               │
└──────────────────┬────────────────────────────────┘
                   │
        ┌──────────┼──────────┐
        │          │          │
   ┌────▼────┐ ┌───▼────┐ ┌──▼──────────┐
   │  Home   │ │Session │ │PluginRoute  │
   │ (首页)  │ │(会话)  │ │(插件路由)    │
   └─────────┘ └───┬────┘ └─────────────┘
                   │
     ┌─────────────┼─────────────┐
     │             │             │
┌────▼────┐ ┌─────▼──────┐ ┌───▼──────┐
│Sidebar  │ │ Messages   │ │ Prompt   │
│(侧边栏) │ │(消息列表)   │ │(输入框)  │
└─────────┘ └────────────┘ └──────────┘
```

### 框架选型：OpenTUI + Solid.js

OpenCode 使用 **OpenTUI** —— 一个基于 Solid.js 响应式系统的终端 UI 框架。与 Ink（React for CLI）类似的理念，但基于 Solid.js 的细粒度响应式更新，避免虚拟 DOM diffing。

**渲染原语：**
```tsx
<box>        // Flex 容器（alignItems, justifyContent, flexGrow, padding 等）
<text>       // 文本渲染（fg, bg, attributes: BOLD/DIM/ITALIC）
<scrollbox>  // 可滚动容器（垂直滚动条）
<textarea>   // 多行文本输入（光标、占位符）
<input>      // 单行输入
<button>     // 可点击按钮
```

**渲染配置（`app.tsx`）：**
```typescript
{
  externalOutputMode: "passthrough",  // 外部输出直通
  targetFps: 60,                      // 60 FPS 目标帧率
  exitOnCtrlC: false,                 // 手动处理 Ctrl+C
  useKittyKeyboard: { events: true }, // Windows 上启用 Kitty 协议
  autoFocus: false,
}
```

---

## 2. Provider 树（状态初始化顺序）

OpenCode 采用 19 层嵌套的 Context Provider，每层职责清晰：

```
App (ErrorBoundary)
├── ArgsProvider          # CLI 参数
├── ExitProvider          # 退出/清理处理器
├── KVProvider            # 键值持久化存储
├── ToastProvider         # 通知系统
├── RouteProvider         # 导航路由
├── TuiConfigProvider     # TUI 配置
├── SDKProvider           # 后端 SDK + SSE 事件
├── SyncProvider          # 数据同步存储（核心）
├── ThemeProvider         # 主题/样式
├── LocalProvider         # Agent/模型选择
├── KeybindProvider       # 快捷键
├── PromptStashProvider   # 输入暂存
├── DialogProvider        # 模态对话框栈
├── CommandProvider       # 命令面板
├── FrecencyProvider      # 频率排序
├── PromptHistoryProvider # 输入历史
├── PromptRefProvider     # 提示引用管理
└── Switch                # 路由分发
    ├── Home
    ├── Session
    └── PluginRoute
```

每个 Provider 遵循统一模式：
```typescript
// createSimpleContext() 工具函数
const [ctx, useXxx] = createSimpleContext<Value>("xxx")
export function XxxProvider(props: ParentProps) {
  const value = /* 初始化逻辑 */
  return <ctx.Provider value={value}>{props.children}</ctx.Provider>
}
```

---

## 3. 核心状态管理 — SyncContext

**文件：** `context/sync.tsx`（500+ 行）

SyncContext 是 TUI 最核心的状态存储，通过 SSE 事件与后端实时同步：

```typescript
type SyncStore = {
  status: "loading" | "partial" | "complete"
  provider: Provider[]              // AI 提供商列表
  agent: Agent[]                    // 可用 Agent
  command: Command[]                // 注册的命令
  config: Config                    // 服务端配置
  session: Session[]                // 所有会话
  message: { [sessionID]: Message[] }
  part: { [messageID]: Part[] }     // 消息组件（文本/工具/思考）
  todo: { [sessionID]: Todo[] }
  permission: { [sessionID]: PermissionRequest[] }
  question: { [sessionID]: QuestionRequest[] }
  session_status: { [sessionID]: SessionStatus }
  session_diff: { [sessionID]: FileDiff[] }
  lsp: LspStatus[]                  // LSP 服务状态
  mcp: { [key]: McpStatus }         // MCP 服务状态
  vcs: VcsInfo | undefined          // Git/VCS 信息
  workspaceList: Workspace[]
}
```

### 数据流

```
后端 SSE 事件
    ↓
sdk.event.on(eventType, handler)
    ↓
事件入队 + 16ms 批量刷新
    ↓
setStore() 更新（二分查找高效定位）
    ↓
Solid.js 信号触发
    ↓
组件自动重渲染（细粒度）
```

**关键优化：**
- 事件批量处理（16ms 间隔），避免高频重渲染
- 数组更新使用二分查找定位
- Solid.js 的 `reconcile()` 进行数组协调

---

## 4. 路由系统

**文件：** `context/route.tsx`

简洁的路由设计，三个页面：

| 路由 | 组件 | 用途 |
|------|------|------|
| `home` | `<Home />` | 会话创建、Logo、初始提示 |
| `session` | `<Session />` | 会话交互（消息列表 + 侧边栏 + 提示输入） |
| `plugin` | `<PluginRoute />` | 插件注册的动态路由 |

```typescript
type Route =
  | { type: "home"; initialPrompt?: string }
  | { type: "session"; sessionID: string; initialPrompt?: string }
  | { type: "plugin"; plugin: string; params?: Record<string, any> }
```

路由切换通过 `route.navigate()` 实现，支持从环境变量 `OPENCODE_ROUTE` 覆盖初始路由。

---

## 5. 会话视图

**文件：** `routes/session/index.tsx`（约 1000 行）

### 布局结构

```
Session 容器（100% 宽高）
├── Sidebar（可选，42 字符宽）
│   ├── 标题 + 分享 URL
│   ├── 插件插槽（文件列表、LSP、MCP、Todos）
│   └── 页脚（版本信息）
└── 主内容区（剩余宽度）
    ├── ScrollBox（消息 + 工具输出）
    │   └── Message 组件（逐条渲染）
    ├── 权限/问答提示
    └── Prompt 输入框（底部）
```

### 消息渲染

| 消息类型 | 渲染内容 |
|----------|----------|
| 用户消息 | 输入文本 + 文件附件 |
| 助手消息 | 思考过程、文本、工具输出 |
| 工具调用 | Read/Write/Bash/Grep 等，带状态指示 |
| Diff | 语法高亮的差异视图 |
| 代码块 | 语言检测 + 语法高亮 |

### 会话状态

```typescript
sidebar: "auto" | "hide"       // 侧边栏可见性
conceal: boolean               // 隐藏思考过程
showThinking: boolean          // 显示思考
showTimestamps: boolean        // 显示时间戳
showDetails: boolean           // 工具调用详情
diffWrapMode: "word" | "none"  // Diff 换行模式
scrollAcceleration: MacOS | Custom  // 滚动加速
```

---

## 6. 输入系统

### Prompt 组件（`component/prompt/index.tsx`，约 800 行）

基于 `<textarea>` 渲染原语的富文本输入：

- 多行输入 + 粘贴支持
- 文件/图片附件渲染
- 自动补全集成（Slash 命令、文件路径、历史记录）
- 暂存/恢复（Stash）功能
- 语法高亮

### 键盘系统（`context/keybind.tsx`，105 行）

```typescript
interface Keybind {
  match(name: string, evt: ParsedKey): boolean  // 匹配快捷键
  parse(evt: ParsedKey): Keybind.Info           // 解析按键
  print(key: string): string                    // 格式化显示
  leader: boolean                               // Leader 键状态
}
```

**特性：**
- **Leader 键**：空格前缀，2 秒超时（类似 Vim Leader）
- **多键组合**：`ctrl+a`、`<leader>x`、`shift+f1`
- **配置来源**：`~/.opencode/config.yaml` → TuiConfig 覆盖
- **Windows 特殊处理**：`win32DisableProcessedInput()` 获取原始输入

### 键盘事件流

```
终端原始输入
    ↓
OpenTUI 解析 → ParsedKey（修饰键: ctrl/shift/alt/meta）
    ↓
useKeyboard() Hook 分发
    ↓
keybind.match(name, evt) 匹配
    ↓
对应处理器执行
```

### 核心快捷键

| 快捷键 | 功能 |
|--------|------|
| `Escape` / `Ctrl+C` | 关闭对话框 |
| 配置的提交键（默认 `Ctrl+Enter`） | 提交提示 |
| `session_interrupt` | 中断 Agent 执行 |
| `command_list` | 打开命令面板 |
| `model_cycle_recent` | 切换最近模型 |
| `model_cycle_favorite` | 切换收藏模型 |
| `Ctrl+C`（有选区时） | 复制选中文本 |

### 文本输入键绑定（`component/textarea-keybindings.ts`）

自定义 `TextareaRenderable` 的键绑定：复制/粘贴/剪切、历史导航（上/下）。

---

## 7. 主题系统

**文件：** `context/theme.tsx`（400+ 行）

### 主题结构

```typescript
type ThemeJson = {
  defs?: Record<string, HexColor | RefName>  // 颜色定义（可引用）
  theme: {
    // 基础色
    primary, secondary, accent,
    error, warning, success, info,
    // 文本
    text, textMuted,
    // 背景
    background, backgroundPanel, backgroundElement,
    // 边框
    border, borderActive, borderSubtle,
    // Diff 颜色（12 个）
    diffAdded, diffRemoved, diffContext, diffHunkHeader,
    diffHighlightAdded, diffHighlightRemoved,
    diffAddedBg, diffRemovedBg, diffContextBg,
    diffLineNumber, diffAddedLineNumberBg, diffRemovedLineNumberBg,
    // Markdown（4 个）
    markdownText, markdownHeading, markdownLink, markdownCode,
    // 语法高亮（8 个）
    syntaxComment, syntaxKeyword, syntaxFunction, syntaxVariable,
    syntaxString, syntaxNumber, syntaxType, syntaxOperator,
    // 可选
    selectedListItemText?, backgroundMenu?, thinkingOpacity?,
  }
}
```

### 内置主题（30 个）

OpenCode, Aura, Ayu, Catppuccin (Frappe/Macchiato/Mocha), Cobalt2, Cursor, Dracula, Everforest, Flexoki, GitHub, Gruvbox, Kanagawa, Material, Matrix, Mercury, Monokai, Nord, One Dark, Osaka Jade, Orng (Lucent), Palenight, Rosepine, Solarized, Synthwave84, Tokyo Night, Vercel, Vesper, Zenburn, Carbonfox

### 颜色解析

```
颜色值（Hex / Ref / Variant）
    ↓
递归解析引用（defs 查找）
    ↓
支持 dark/light 变体
    ↓
Hex → RGBA 转换
    ↓
自动对比度计算（选中项前景色）
```

### 使用方式

```tsx
const { theme } = useTheme()
<box backgroundColor={theme.backgroundPanel}>
  <text fg={theme.text}>内容</text>
</box>
<scrollbar foregroundColor={theme.borderActive} />
```

主题加载顺序：内置默认 → 插件主题 → 自定义主题（`~/.opencode/themes/`）→ 运行时系统主题

---

## 8. 对话框系统

### 对话框框架（`ui/dialog.tsx`）

```typescript
interface DialogContext {
  show(dialog: JSX.Element, size?: DialogSize): void
  replace(dialog: JSX.Element, size?: DialogSize): void
  clear(): void
}
```

- 对话框栈管理（支持叠加）
- 暗化遮罩 + 居中显示
- 鼠标点击遮罩关闭
- 焦点管理

### 对话框类型（15+ 种）

| 对话框 | 用途 |
|--------|------|
| `DialogSelect` | 通用选择列表（带过滤） |
| `DialogModel` | 模型/提供商选择 |
| `DialogProvider` | 提供商配置 |
| `DialogSessionList` | 打开历史会话 |
| `DialogAgent` | Agent 选择 |
| `DialogCommand` | 命令面板（类 VS Code） |
| `DialogConfirm` | 确认操作 |
| `DialogAlert` | 警告提示 |
| `DialogPrompt` | 文本输入 |
| `DialogMcp` | MCP 服务管理 |
| `DialogStatus` | 状态/进度显示 |
| `DialogThemeList` | 主题切换器 |
| `DialogMessage` | 消息详情 |
| `DialogTimeline` | 时间线视图 |
| `DialogForkFromTimeline` | 从时间线分支 |

---

## 9. 插件系统

**文件：** `plugin/runtime.ts`（700 行）, `plugin/api.tsx`, `plugin/slots.tsx`

### 插件 API

```typescript
interface TuiPluginApi {
  app: { version: string }
  command: { register(), trigger() }
  route: { register(), navigate(), current() }
  dialog: { alert(), confirm(), select(), prompt() }
  keybind: { listen(), register() }
  theme: { list(), set(), install() }
  kv: { get(), set() }
  toast: { show(), error() }
  state: { ready, config, provider, session, part, mcp, lsp }
  renderer: { currentFocusedRenderable, requestRender() }
  scopedClient(): OpencodeClient
  workspace: { current(), set() }
  lifecycle: { signal, onDispose() }
}
```

### 插件插槽

命名的扩展点，插件可注册组件到指定位置：

| 插槽名 | 位置 | 模式 |
|--------|------|------|
| `home_logo` | 首页 Logo | replace（单个） |
| `home_prompt` | 首页提示 | replace |
| `home_footer` | 首页页脚 | merge（多个） |
| `sidebar_title` | 侧边栏标题 | replace |
| `sidebar_content` | 侧边栏内容 | merge |
| `sidebar_footer` | 侧边栏页脚 | merge |

### 插件来源

- 内置插件（`feature-plugins/`）：侧边栏文件列表、LSP 状态、MCP 状态、Todo 列表
- 外部插件：npm 包、git 仓库
- 生命周期：Load → Register → Enable → Dispose（5 秒超时）

---

## 10. 事件系统

### SDK 事件（后端 → 前端）

```typescript
// 核心事件类型
"session.updated" / "session.deleted" / "session.diff" / "session.status"
"message.updated" / "message.deleted"
"message.part.updated"
"permission.asked" / "permission.replied"
"question.asked" / "question.replied" / "question.rejected"
"todo.updated"
"server.instance.disposed"
```

### TUI 事件（前端内部）

```typescript
TuiEvent.PromptAppend:   { text: string }        // 向提示追加文本
TuiEvent.CommandExecute: { command: string }      // 执行命令
TuiEvent.ToastShow:      { title?, message, variant, duration? }
TuiEvent.SessionSelect:  { sessionID }            // 选择会话
```

### 事件处理流程

```
SSE 事件流
    ↓
sdk.event.on(type, handler)
    ↓
事件入队（queue）
    ↓
16ms 批量刷新（flush）
    ↓
setStore() 更新 SyncStore
    ↓
Solid.js 细粒度信号触发
    ↓
仅受影响的组件重渲染
```

---

## 11. Toast 通知

**文件：** `ui/toast.tsx`

```typescript
interface ToastContext {
  show(options: {
    title?: string
    message: string
    variant: "info" | "success" | "warning" | "error"
    duration?: number  // 自动消失时间
  }): void
}
```

渲染为绝对定位的浮层，自动消失。

---

## 12. 持久化存储

### KV 存储（`context/kv.tsx`）

持久化到 `kv.json`，存储 UI 状态偏好：

- 终端标题开关
- 侧边栏状态（auto/hide）
- 动画开关
- 思考过程可见性
- 时间戳显示
- 滚动条可见性
- 当前主题

### Frecency 排序（`context/frecency.tsx`）

追踪命令/提示的使用频率与近期度，用于智能排序：
- 频繁使用的命令排在前面
- 带有时间衰减的频率评分

### 提示历史（`context/prompt-history.tsx`）

- 持久化到本地存储
- 上/下键导航历史
- Frecency 排序建议

---

## 13. 跨平台支持

### Windows 特殊处理（`win32.ts`）

- `win32DisableProcessedInput()` — 禁用处理模式以获取原始键盘输入
- Ctrl+C guard — 安装/卸载 Ctrl+C 拦截
- Kitty 键盘协议兼容

### 终端检测（`util/terminal.ts`）

- 背景颜色检测（dark/light 自动切换）
- ANSI 调色板索引支持

### 外部编辑器（`util/editor.ts`）

- `$EDITOR` 环境变量支持
- Vim/Emacs 多行编辑

---

## 14. 启动流程

```
tui() 入口函数
    ↓
1. 安装 Ctrl+C guard（Windows）
2. 禁用处理输入（Windows）
3. 检测终端背景色（dark/light）
4. createCliRenderer(config)  // 60 FPS, Kitty 协议
5. render(App, renderer)       // 挂载 Provider 树
    ↓
初始同步：
  "loading" → "partial"（会话列表）→ "complete"（全部数据）
    ↓
路由分发：
  默认 → Home | 指定 → Session/Plugin
    ↓
退出时：
  卸载 Ctrl+C guard → 销毁插件 → resolve Promise
```

---

## 15. 性能优化

| 策略 | 说明 |
|------|------|
| **60 FPS 目标** | `targetFps: 60`，OpenTUI 渲染器级别控制 |
| **Solid.js 细粒度响应式** | 无虚拟 DOM diffing，仅受影响的 DOM 节点更新 |
| **事件批量处理** | 16ms 间隔批量刷新 SSE 事件，避免高频重渲染 |
| **二分查找更新** | SyncStore 数组更新使用二分查找定位元素 |
| **reconcile()** | Solid.js 数组协调，最小化更新 |
| **滚动加速** | macOS 原生滚动加速 / 自定义速度滚动 |
| **按需渲染** | 对话框/侧边栏按可见性条件渲染 |

---

## 16. 关键设计决策

1. **OpenTUI + Solid.js** — 细粒度响应式替代 React 虚拟 DOM，终端渲染更高效
2. **19 层 Provider 树** — 每个关注点独立，依赖注入清晰，但嵌套较深
3. **SyncStore 中心化** — 单一数据源，SSE 事件驱动，保证 UI 与后端一致
4. **插件插槽系统** — 命名扩展点 + replace/merge 模式，灵活的可扩展性
5. **30+ 内置主题** — JSON 定义 + 引用系统 + dark/light 变体，开箱即用
6. **Leader 键** — 类 Vim 的空格前缀快捷键，扩展快捷键空间
7. **事件批量刷新** — 16ms 间隔防止 SSE 高频更新导致卡顿
8. **Frecency 排序** — 频率 + 近期度结合，智能排序命令和历史

---

## 17. 三款 CLI TUI 对比

| 维度 | Claude Code | Kimi-CLI | OpenCode |
|------|-------------|----------|----------|
| 语言 | TypeScript | Python 3.12+ | TypeScript (Bun) |
| UI 框架 | 自定义 Ink（React reconciler） | Prompt Toolkit + Rich | OpenTUI（Solid.js） |
| 响应式模型 | React（虚拟 DOM diff） | 命令式 + async | Solid.js（细粒度信号） |
| 渲染方式 | 自定义帧差分 + 双缓冲 | Rich Live + 增量 Markdown 提交 | OpenTUI 渲染器（60 FPS） |
| 布局引擎 | Yoga Flexbox | PTK HSplit/VSplit | OpenTUI Flex |
| 状态管理 | Zustand | Shell 实例属性 | Solid.js signals/stores |
| 主题数量 | 暗/亮 2 种 | 暗/亮 2 种 | **30+ 内置主题** |
| Vim 支持 | 完整状态机 | 无 | Leader 键 |
| 插件系统 | Skills 系统 | Slash 命令 | **完整插件 API + 插槽** |
| 鼠标支持 | 完整（选择/点击/拖拽） | 终端原生 | 点击 + 右键选择 |
| 目标帧率 | 30 FPS | N/A（事件驱动） | **60 FPS** |
| 复杂度 | 极高（自建渲染层） | 中等（组合成熟库） | 高（现代框架 + 插件体系） |
| 设计哲学 | 从底层构建完全控制 | 组合成熟库快速实现 | 现代框架 + 可扩展架构 |
| 代码量（TUI） | ~250KB+ 核心渲染器 | ~9,300 行 | ~14,600 行 |
