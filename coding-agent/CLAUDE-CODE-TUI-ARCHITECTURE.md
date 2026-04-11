# Claude Code TUI 架构分析

基于 `@anthropic-ai/claude-code` v2.1.88 源码还原的 TUI（终端用户界面）设计总结。

## 技术栈概览

| 层级 | 技术 |
|------|------|
| 渲染引擎 | **Ink** — 基于 `react-reconciler` 的自定义终端 React 渲染器 |
| 布局引擎 | **Yoga** — Facebook 的跨平台 CSS Flexbox 实现 |
| 状态管理 | **Zustand** — 轻量响应式状态存储 |
| 输入处理 | 自定义键盘解析器（支持 Kitty 协议、xterm、Vim 模式） |
| 样式系统 | ANSI 转义序列 + 主题上下文 |

## 目录结构

| 目录 | 职责 |
|------|------|
| `ink/` | 终端渲染器核心：帧管理、屏幕缓冲、布局、样式 |
| `ink/components/` | Ink 基础组件：Box, Text, Button, ScrollBox, AlternateScreen |
| `ink/hooks/` | Ink 层 Hooks：useInput, useSelection, useTerminalViewport |
| `ink/events/` | 事件系统：EventEmitter, 键盘/鼠标/焦点事件 |
| `ink/layout/` | Yoga 集成：Flexbox 布局计算 |
| `ink/termio/` | 终端 I/O：ANSI 转义序列解析、终端能力检测 |
| `components/` | 高层 UI 组件：对话框、列表、输入框等（146 个文件） |
| `screens/` | 顶层屏幕：REPL、Doctor、ResumeConversation |
| `hooks/` | 应用层 Hooks：快捷键、vim、输入处理（87 个文件） |
| `state/` | Zustand 状态存储：AppState、选择器、变更处理器 |
| `keybindings/` | 快捷键系统：解析、匹配、上下文优先级 |
| `vim/` | Vim 模式：状态机、操作符、移动命令 |
| `outputStyles/` | 自定义输出样式（通过 Markdown 文件） |

---

## 1. 渲染引擎 — Ink

### 核心类 `Ink`（`ink/ink.tsx`，约 251KB）

Ink 是整个 TUI 的中心调度器，职责包括：
- 通过 `react-reconciler` 管理 React 协调
- 控制 stdin/stdout/stderr 流
- 管理原始终端模式（raw mode）
- 帧渲染与差分更新
- 键盘/鼠标输入事件处理
- 光标定位与可见性控制

### DOM 模型（`ink/dom.ts`）

自定义 DOM 节点类型：
- `ink-root` — 根节点
- `ink-box` — 布局容器（类似 `<div>`）
- `ink-text` / `ink-virtual-text` — 文本节点
- `ink-link` — OSC 8 超链接
- `ink-progress` — 进度条
- `ink-raw-ansi` — 原始 ANSI 输出

节点支持：
- 滚动状态：`scrollTop`, `pendingScrollDelta`, `scrollHeight`, `scrollViewportHeight`
- 滚动锚定：`scrollAnchor` 元素级相对滚动
- 定位：`position: absolute/relative` + top/bottom/left/right
- 焦点系统：`focusManager` 管理 Tab 导航

### 屏幕缓冲（`ink/screen.ts`）

**内存优化 — 字符串池化：**

| 池 | 作用 |
|----|------|
| **CharPool** | 字符intern（空格/空字符索引 0-1，ASCII 快速路径，其余用 Map） |
| **StylePool** | 样式组合 intern 为 ANSI 序列，缓存样式转换 |
| **HyperlinkPool** | OSC 8 超链接 URI intern |

所有池跨帧共享，ID 稳定，支持直接 blit。

**单元格结构：**
```typescript
Cell = { char: number, styleId: number, hyperlink: number, width: CellWidth }
// CellWidth: Normal(0), WideStart(1), WideTail(2), SpacerTail(3)
```
支持 CJK 等双宽字符。

### 渲染管线

```
React 协调 → DOM 树变更
    ↓
resetAfterCommit() → Yoga 布局计算
    ↓
createRenderer() → DOM 转换为 Screen 缓冲
    ↓
LogUpdate.render() → 新旧帧差分 → 生成 Patch 列表
    ↓
optimize() → 合并/去重 Patch
    ↓
writeDiffToTerminal() → 序列化为 ANSI → 写入 stdout
```

**Patch 类型：**
- `stdout` — 文本内容输出
- `clear` / `clearTerminal` — 清屏
- `cursorHide` / `cursorShow` / `cursorMove` — 光标操作
- `hyperlink` — OSC 8 超链接
- `styleStr` — 预序列化 ANSI 样式转换

### 双缓冲

`frontFrame` / `backFrame` 双缓冲机制防止画面闪烁。渲染时写入后缓冲，差分完成后交换。

---

## 2. 布局引擎 — Yoga

通过 `ink/layout/` 集成 Facebook 的 Yoga 引擎，提供完整的 CSS Flexbox 支持：

- `LayoutNode` 包装 Yoga 节点并附加元数据
- 支持 `overflow: scroll` 及自定义滚动状态管理
- 支持 `display: none` 节点跳过计算
- 缓存布局结果，仅在脏标记时重新计算

支持的布局属性：flexDirection, justifyContent, alignItems, flexGrow, flexShrink, flexWrap, gap, margin, padding, width, height, minWidth, maxWidth, position 等。

---

## 3. 组件体系

### 基础组件（`ink/components/`）

| 组件 | 职责 |
|------|------|
| **App.tsx**（98KB） | 根组件：stdin/stdout 上下文、Ctrl+C 退出、键盘输入分发、鼠标事件、终端模式断言 |
| **Box.tsx** | 布局原语（类似 `<div style="display:flex">`），支持 flex、边距、边框、定位、事件 |
| **Text.tsx** | 文本渲染，支持 textWrap（wrap/truncate/middle）、样式（bold/dim/color 等） |
| **ScrollBox.tsx**（31KB） | 虚拟滚动容器，支持粘性滚动、每帧最大滚动量限制、元素级滚动定位 |
| **AlternateScreen.tsx** | 全屏模式（DEC 1049），启用 SGR 鼠标追踪 |
| **Button.tsx** | 可点击元素，支持 hover/focus/active 状态 |
| **Link.tsx** | OSC 8 超链接 |

### 高层组件（`components/`，146 个文件）

包含对话框、Toast、通知、消息列表、工具调用展示、权限确认、代码差分等业务组件。依赖主题系统进行样式化。

### 屏幕组件（`screens/`）

| 屏幕 | 职责 |
|------|------|
| **REPL.tsx**（5005 行） | 主交互循环：消息渲染、提示输入、快捷键、Agent 通信、工具权限对话、进度指示 |
| **Doctor.tsx** | 诊断与修复工具 |
| **ResumeConversation.tsx** | 恢复历史会话 |

### 组件层级

```
<ink.render()>
  └─ <ThemeProvider>
      └─ <App>  (上下文设置)
          └─ <AppStateProvider>
              └─ <StatsProvider>
                  └─ <FpsMetricsProvider>
                      └─ <REPL>
                          ├─ <AlternateScreen>  (全屏模式)
                          │   ├─ <FullscreenLayout>
                          │   └─ <ScrollKeybindingHandler>
                          ├─ <Messages>  (VirtualMessageList)
                          │   └─ [消息组件...]
                          ├─ <PromptInput>
                          │   └─ <BaseTextInput>
                          ├─ [对话框、Toast、通知]
                          └─ <CompanionSprite>  (buddy AI)
```

---

## 4. 状态管理 — Zustand

### AppState 核心状态（`state/AppStateStore.ts`）

```typescript
type AppState = {
  // 对话
  messages: Message[]
  streamingMessage?: Message

  // 输入
  promptInput: string
  promptInputMode: PromptInputMode
  vimMode: VimMode

  // UI 状态
  selectedMessageIdx?: number
  expandedToolUseIds: Set<string>

  // 工具权限
  toolPermissionContext: ToolPermissionContext

  // 会话
  sessionId: string
  sessionTitle?: string

  // ... 更多字段：费用、任务、规格等
}
```

- 通过 `createStore()` 创建
- `AppStateProvider` 上下文注入
- `useAppState(selector)` — 选择器模式，仅在值变更时触发重渲染（`Object.is` 比较）
- `useSetAppState()` — 获取 setState
- `onChangeAppState(old, new)` — 全局变更回调（用于分析、验证）

---

## 5. 输入处理

### 键盘输入流程

```
终端 stdin 原始字节
    ↓
parse-keypress.ts: 字节 → ParsedKey
    ↓
App.tsx handleData()
    ↓
EventEmitter.emit('input', InputEvent)
    ↓
多个监听器并行处理：
  ├─ useInput() — 组件级处理
  ├─ Vim 状态机 — INSERT/NORMAL 模式分发
  ├─ Keybinding 解析器 — 快捷键 → action 名称
  └─ 全局快捷键处理器
    ↓
Action 调用 → 组件状态更新 → React 重渲染
```

### 键盘解析（`ink/parse-keypress.ts`，23KB）

支持多种终端协议：
- **Kitty 键盘协议**（CSI u）
- **xterm modifyOtherKeys**（CSI 27; modifier; code ~）
- **SGR 鼠标事件**（CSI < button; col; row M/m）
- **终端响应解析**：DECRPM, DA1/DA2, 光标位置, XTVERSION
- **括号粘贴模式**（Bracketed Paste）

### 快捷键系统（`keybindings/`）

**KeybindingContext** — 中央注册表：
- `resolve(input, key, contexts)` → action 名称
- 支持和弦状态（多键序列，如 `ctrl+g a`）
- 多个活跃上下文，按优先级排序
- `registerHandler(action, context, callback)` / `invokeAction(action)`

**配置来源：**
- `defaultBindings.ts` — 内置默认快捷键
- `~/.claude/keybindings.json` — 用户自定义
- 快捷键格式：`"ctrl+s"`, `"shift+enter"`, `"ctrl+g a"`（和弦）

### Vim 模式（`vim/`）

**状态机设计：**
```typescript
type VimState =
  | { mode: 'INSERT'; insertedText: string }
  | { mode: 'NORMAL'; command: CommandState }
```

**CommandState 子状态：**
- `idle` → 等待输入
- `count` → 累积数字计数
- `operator` → 操作符后等待动作（d/c/y）
- `operatorCount` → 操作符 + 计数
- `operatorFind` → 操作符 + f/F/t/T
- `operatorTextObj` → 操作符 + i/a + 文本对象
- `find` → f/F/t/T（无操作符）
- `g` → g 模式（gg, gj 等）
- `replace` → r 模式
- `indent` → </>  模式

**操作符**：delete, change, yank
**动作**：h/j/k/l, w/b/e/W/B/E, 0/^/$, f/F/t/T, 文本对象（iw/aw/ib/ab 等）

**持久状态：**
```typescript
type PersistentState = {
  lastChange: RecordedChange | null  // 点重复（.）
  lastFind: FindType & char          // 查找重复（;, ,）
  register: string                   // 粘贴缓冲区
  registerIsLinewise: boolean
}
```

---

## 6. 文本选择与搜索

### 文本选择（`ink/selection.ts`，34KB）

- 点击开始 → 拖拽扩展 → 释放冻结
- 多次点击：双击选词、三击选行
- 渲染时反色覆盖选中单元格
- 滚动时 `shiftSelection()` 保持选区锚定
- 支持 copy-on-select 复制到剪贴板

### 搜索高亮（`ink/searchHighlight.ts`）

- 扫描 DOM 匹配查询文本
- 存储位置（消息相对坐标，滚动后不变）
- 黄色反色高亮（SGR 7 + SGR 33）

---

## 7. 主题与样式

### 主题系统（`components/design-system/`）

- `ThemeProvider` 包裹所有渲染，注入主题上下文
- `useTheme()` Hook 获取当前主题
- 支持暗色/亮色模式
- 语义化颜色映射到 ANSI/hex 值

**文本样式属性：**
color, backgroundColor, bold, dim, italic, underline, strikethrough, inverse

**布局样式属性：**
margin/padding（含方向变体）, gap, width/height（含 min/max）, flex 系列, position, border 系列

### 输出样式（`outputStyles/`）

- 从 `.claude/output-styles/` 加载 Markdown 文件
- 前置元数据：name, description, keep-coding-instructions
- 项目级覆盖用户级配置
- 作为提示词附加到系统提示

---

## 8. 性能优化

### 渲染优化

| 策略 | 说明 |
|------|------|
| **双缓冲** | frontFrame/backFrame 防止闪烁 |
| **字符串池化** | CharPool/StylePool/HyperlinkPool 减少内存分配 |
| **Blit 优化** | 布局未偏移时，仅差分变更单元格 |
| **硬件滚动** | DECSTBM + SU/SD 用于分页（非全帧重写） |
| **延迟布局** | Yoga 节点缓存，仅脏标记时重计算 |
| **文本测量缓存** | 避免重复 wrap 未变更文本 |
| **Patch 合并/去重** | optimizer 减少冗余输出 |
| **选区保持** | captureScrolledRows 在帧交换前读取旧帧 |

### 帧率管理

- 目标帧率：**30 FPS**（FRAME_INTERVAL_MS = 33ms）
- `RequestIdleCallback` 批量渲染
- 节流渲染防止每帧重复输出
- Yoga 计数器追踪访问节点数和测量调用数

### 内存效率

- 字符串 intern：每唯一字符串一个实例，跨帧复用
- `useEventCallback` 保持稳定引用
- `useAppState(selector)` 仅在值变更时触发重渲染
- `IDLE_SPECULATION_STATE` 复用冻结对象

---

## 9. 关键设计决策

1. **自定义 React Reconciler** — 完全控制渲染管线，不受 React DOM 限制
2. **双缓冲 + Blit 差分** — 终端渲染无闪烁，最小化 I/O
3. **Yoga Flexbox** — 跨平台、经过实战检验的布局引擎
4. **池化字符串 Intern** — 减少 GC 压力，支持高效单元格复制
5. **Vim 穷举类型状态机** — TypeScript 类型系统确保状态转换正确性
6. **Zustand** — 简洁高效的状态管理，无模板代码
7. **备用屏幕（Alt Screen）** — 保留主屏幕内容，支持覆盖层
8. **硬件滚动（DECSTBM）** — 高效分页，无需全帧重写
9. **Kitty 键盘协议** — 现代终端精确键盘输入
10. **和弦快捷键** — 多键序列支持扩展快捷键空间
