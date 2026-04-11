# Coding Agent TUI 实现要点清单

## Context

基于对 Claude Code、Kimi CLI、OpenCode 三款生产级 TUI 的深度源码分析，整理出构建一个 coding agent TUI 需要实现的完整要点。按层级从底层到高层组织，标注优先级（P0 必须/P1 重要/P2 锦上添花），并对比三款产品的实现差异和关键设计决策。

---

## Layer 0: 终端 I/O 基础

### 0.1 原始 stdin/stdout 管理 [P0]
终端进入 raw mode，管理字节流读写。

| 产品 | 实现 |
|------|------|
| Claude Code | 自建 `termio/` 模块，Tokenizer 将字节流切分为转义序列 token，Parser 转为结构化 Action |
| Kimi CLI | 委托给 Prompt Toolkit 的 VT100 解析器 + termios/msvcrt |
| OpenCode | Bun 原生 API + OpenTUI 抽象层 |

**决策点：** 自建解析器（完全控制边界情况如 tmux/SSH）vs 使用库（覆盖 90% 场景）

### 0.2 键盘输入解析 [P0]
将原始转义序列转为带修饰键标志的结构化按键事件。

需支持的协议层：
- 传统 VT 序列（功能键、方向键）
- **Kitty 键盘协议**（CSI u）— 区分 Ctrl+Shift+字母等传统不可区分的键
- **xterm modifyOtherKeys**（CSI 27;modifier;code~）
- **SGR 鼠标事件**（CSI < button;col;row M/m）— 滚轮、点击、拖拽
- **括号粘贴模式**（mode 2004）— 防止粘贴代码被当作命令
- 终端响应序列（DECRPM, DA1/DA2, XTVERSION, 光标位置）

### 0.3 终端能力检测 [P0]
启动时探测终端支持的特性。

**Claude Code 的哨兵方案（最佳实践）：** 发送查询 + DA1 哨兵，等待响应。DA1 之前未收到某查询的响应 = 不支持。无需超时，不受 SSH 延迟影响。

需探测：Kitty 键盘、同步输出、背景色（dark/light 检测）、终端身份（XTVERSION）、DEC 私有模式。

### 0.4 屏幕缓冲 / 单元格网格 [P0]
2D 单元格网格表示终端屏幕。

**Claude Code 方案（高性能）：**
- `Uint32Array` 每单元格 4 个整数：`[charId, styleId, CellWidth, hyperlinkId]`
- 字符串池化：`CharPool`（ASCII 快速路径）、`StylePool`（ANSI 组合）、`HyperlinkPool`（OSC 8 URI）
- 每单元格 ~16 字节，零 GC 压力。200×50 终端仅 160KB

**决策点：** 整数 ID 单元格（高性能）vs 对象单元格（简单但内存大、GC 重）

---

## Layer 1: 渲染管线

### 1.1 布局引擎 [P0]
计算每个 UI 元素的位置和尺寸。

| 产品 | 实现 |
|------|------|
| Claude Code | Yoga Flexbox（C++ 原生依赖），完整 CSS 盒模型 |
| Kimi CLI | PTK HSplit/VSplit + Rich Console 宽度换行 |
| OpenCode | OpenTUI 内置 Flex |

**决策点：** Yoga Flexbox（强大但有原生依赖）vs 手写约束布局（简单但有限）vs 框架内置

### 1.2 渲染 / 绘制 [P0]
将布局树转为屏幕缓冲区的单元格写入。

Claude Code 使用操作式渲染：发射 Operation 对象（write/clip/unclip/blit/clear/shift），然后统一应用到缓冲区。支持裁剪嵌套和滚动区域复用。

**关键优化：** 按行字符缓存（行内容 → `ClusteredChar[]`），避免对未变化行重复计算字素分割和宽度。

### 1.3 双缓冲与差分 [P0]
维护前/后两块屏幕缓冲，仅输出变化的单元格。

- 单元格级 diff：整数比较 charId+styleId+hyperlinkId，200×50 = 10,000 次比较，亚毫秒
- 生成 Patch（stdout/cursorMove/style/hyperlink/clear）
- **Patch 优化器：** 合并相邻 stdout、折叠连续 cursorMove、去重 style/hyperlink、取消配对的 cursorHide/cursorShow

### 1.4 帧率控制 [P1]
节流渲染，批量变更。

- Claude Code: 30 FPS，滚动按帧限速（`SCROLL_MAX_PER_FRAME`），防止一帧跳到底
- OpenCode: 60 FPS，16ms 事件批量刷新

**决策点：** 30 FPS 对终端 UI 足够，60 FPS 浪费 CPU 且多数终端模拟器无法显示

### 1.5 硬件滚动优化 (DECSTBM) [P2]
使用终端滚动区域命令（DECSTBM + SU/SD）代替全帧重绘。仅 Claude Code 实现。

---

## Layer 2: 组件模型

### 2.1 组件树 / DOM [P0]
定义基础 UI 元素集合。

Claude Code 最小集：7 种节点类型（root/box/text/virtual-text/link/progress/raw-ansi）。
**实际最小可行集：** Box（flex 容器）+ Text（样式文本）+ ScrollBox（溢出滚动）

### 2.2 Box / Flex 容器 [P0]
主布局原语：direction, wrap, padding, margin, gap, alignment, border, overflow。

### 2.3 Text / 样式文本 [P0]
颜色、粗体、斜体、下划线、暗淡、反色、删除线。文字换行、文本测量、字素分割。

**关键：**
- `Intl.Segmenter` 处理 emoji/CJK 字素分割
- 软换行追踪（区分换行和自动折行），确保复制粘贴正确

### 2.4 ScrollBox / 可滚动容器 [P0]
**粘性滚动（Sticky Scroll）** — 对 coding agent 最重要的滚动行为：
- AI 流式输出时自动跟随最新内容
- 用户向上滚动时断开粘性
- 滚回底部时重新启用

Claude Code 还实现了：滚动钳制（防止空白）、视口裁剪（仅渲染可见子节点）、逐帧滚动漏斗

### 2.5 焦点管理 [P1]
Tab/Shift-Tab 导航，焦点栈（模态推入/弹出），focus/blur 事件。

---

## Layer 3: 状态管理

### 3.1 应用状态存储 [P0]

| 产品 | 实现 |
|------|------|
| Claude Code | 35 行 `createStore<T>`（getState/setState/subscribe），Object.is 变更检测 |
| Kimi CLI | Shell 类实例属性 + async 事件路由 |
| OpenCode | Solid.js signals/stores + SSE 同步 |

**最小状态：** `messages[]`, `isStreaming`, `pendingPermission`, `inputMode`, `model`, `cost`

### 3.2 消息模型 [P0]
- 消息不可变（append-only），缓存正确性依赖此
- 流式消息特殊处理：可变的"当前流式消息"槽位
- 工具调用与工具结果逻辑配对（UI 需关联显示 spinner → 结果）

---

## Layer 4: 输入系统

### 4.1 文本输入 / 提示编辑器 [P0]
多行编辑、光标移动、undo/redo、剪贴板、括号粘贴。

**决策点：**
- Enter 提交 vs Shift+Enter 换行（或可配置）
- 光标在首/末行时，上/下键切换为历史导航
- 大粘贴截断显示不截断数据

**Kimi CLI 独特功能：** Agent 执行期间提示保持活跃，接受实时转向（steer）输入

### 4.2 Vim 模式 [P2]
完整状态机：INSERT/NORMAL，操作符 d/c/y，动作 w/b/e/0/$，文本对象 iw/aw，计数，点重复，查找 f/F/t/T。仅 Claude Code 实现，约 5 个文件。

### 4.3 快捷键系统 [P1]
- 上下文感知解析（同一键在不同上下文含义不同）
- 和弦支持（多键序列如 `ctrl+g a`）或 Leader 键（OpenCode 的空格前缀）
- 用户自定义（JSON 文件覆盖默认绑定）
- 保留键保护（ctrl+c/ctrl+d 不可重绑）

### 4.4 Slash 命令 [P1]
声明式元数据（name/description/aliases/args）+ 处理函数。自动补全和帮助。

---

## Layer 5: 内容渲染

### 5.1 Markdown 渲染 [P0]
标题、代码块、列表、表格、内联格式、链接。

**流式 Markdown 是核心难题：**
- **Kimi CLI 方案（推荐）：** 增量提交 — 解析流式文本，完整块永久打印，未完成尾部保留在瞬态区
- **Claude Code 方案：** 每次更新重新解析全部内容，但用 LRU 缓存（max 500 entries, content hash key）
- 未关闭的代码围栏仍应渲染为代码

### 5.2 代码语法高亮 [P1]
异步加载高亮器（grammar 初始化可能 100ms+），Suspense 边界不阻塞首帧。

### 5.3 Diff 渲染 [P1]
统一 diff + 词级内联高亮（Kimi CLI 用 SequenceMatcher，相似度 > 0.5 时启用词级 diff）。

### 5.4 ANSI 直通 [P1]
工具输出（`ls --color`、`git diff`、编译错误）可能包含任意 ANSI 序列，需真正的解析器而非正则剥离。

---

## Layer 6: Agent 交互

### 6.1 权限 / 审批系统 [P0]
**工具专属预览（关键 UX）：**
- bash → 显示命令
- file_edit → 显示 diff
- file_write → 显示内容预览

**选项：** 批准一次 / 本会话批准 / 拒绝 / 拒绝并说明原因

**异步协调：** Agent 循环等待权限决策，UI 保持交互（ApprovalBridge 模式）

### 6.2 流式响应显示 [P0]
- 粘性滚动自动跟随
- 渐进式 Markdown 渲染
- 工具调用时显示 spinner / 状态行
- 工具结果内联或折叠显示

### 6.3 费用 / Token 追踪 [P1]
状态栏显示费用，可配置阈值警告。

### 6.4 会话管理 [P1]
保存、恢复、浏览会话历史。

---

## Layer 7: 虚拟滚动与性能

### 7.1 虚拟消息列表 [P1, 长对话时为 P0]
仅挂载可见消息 + 超扫描区。Claude Code 参数：
- 默认估高 3 行，超扫描 80 行，冷启动 30 条
- 滚动量化 40px（减少 React 提交）
- 最大挂载 300 条，每次提交最多新挂载 25 条

### 7.2 搜索与高亮 [P2]
全对话搜索 + 匹配导航（n/N）+ 增量搜索。Claude Code 将每条消息渲染到离屏缓冲区以获取精确高亮坐标。

### 7.3 文本选择 [P2]
鼠标拖选、双击选词、三击选行、copy-on-select。需追踪拖拽滚动时离开视口的文本（scrolledOff 累加器）。

---

## Layer 8: 主题与样式

### 8.1 主题系统 [P1]
- 语义色（error/warning/success/muted/accent），不在 UI 代码中写具体颜色
- Dark/light 自动检测（OSC 11 背景色查询）
- JSON 定义主题（OpenCode 有 30+ 内置主题）

### 8.2 OSC 8 超链接 [P2]
终端内可点击链接。需检测终端支持。

---

## Layer 9: 应用外壳

### 9.1 主布局（REPL 编排器）[P0]
顶层组件：消息区（可滚动）+ 提示输入（固定底部）+ 状态栏 + 权限对话框 + 模态覆盖。

**决策点：** 全屏（备用屏幕）vs 内联（滚动回放）。全屏给你完全控制但丢失 scrollback。Claude Code 两者都支持。

### 9.2 状态栏 [P1]
模型名、费用、模式指示、快捷键提示。

### 9.3 模态 / 对话框系统 [P1]
设置、模型选择器、命令面板、导出等。OpenCode 有 15+ 种对话框。

---

## Layer 10: 平台与集成

### 10.1 MCP 集成 [P1→P0]
连接 MCP 服务器提供工具、资源、提示模板。

### 10.2 配置系统 [P1]
分层配置：默认 < 全局 < 项目 < CLI 参数。

### 10.3 插件系统 [P2]
OpenCode 有完整插件 API + 命名插槽（home_logo, sidebar_content 等）。

### 10.4 外部编辑器 [P2]
`$EDITOR` 集成，编辑长提示或审查 diff。

---

## 实现分期建议

### Phase 1 — 最小可行 Agent TUI（4-6 周）
1. 终端 I/O + 键盘解析（0.1, 0.2）
2. 屏幕缓冲 + 单元格网格（0.4）
3. 简单 box/text 布局（2.1-2.3）
4. 双缓冲 + 差分（1.3）
5. 文本输入 + 提交（4.1）
6. 消息列表 + Markdown 渲染（5.1）
7. 权限对话框（6.1）
8. 流式显示 + 自动滚动（6.2）
9. 应用状态存储（3.1）

### Phase 2 — 完善产品（4-6 周）
1. Flexbox 布局引擎（1.1）
2. 终端能力检测（0.3）
3. ScrollBox + 粘性滚动（2.4）
4. 快捷键系统 + 自定义（4.3）
5. Slash 命令（4.4）
6. 主题系统 + dark/light（8.1）
7. 代码高亮（5.2）
8. Diff 渲染（5.3）
9. 费用追踪（6.3）
10. 会话管理（6.4）

### Phase 3 — 打磨与高级功能（持续）
虚拟滚动、帧率控制、硬件滚动、文本选择、Vim 模式、搜索高亮、OSC 8 超链接、MCP 集成、插件系统、外部编辑器

---

## 核心权衡总结

| 决策 | 选项 A | 选项 B | 建议 |
|------|--------|--------|------|
| 框架 | 自建渲染器 | 使用现有框架（Ink/Textual/BubbleTea） | Phase 1 用现有框架，碰壁再考虑自建 |
| 布局 | Yoga Flexbox | 手写约束 | 复杂布局用 Yoga，简单 UI 手写 |
| 响应式 | React reconciler | Solid.js signals | React 生态好，Solid 性能好，都可 |
| 语言 | TypeScript | Python / Go | TS 终端控制深，Python 原型快，Go 适合分发 |
| 屏幕模型 | 整数池化单元格 | 对象单元格 | 规模化用整数，简单场景用对象 |
| 流式 Markdown | 增量提交（Kimi） | 全量重解析+缓存（Claude Code） | 流式用增量提交，虚拟滚动用缓存 |
| 权限 UI | 工具专属预览 | 通用批准/拒绝 | 必须工具专属，这是信任和焦虑的分界线 |
| 滚动 | 虚拟 + 硬件提示 | 全量渲染 | 长对话（>50 条含工具输出）必须虚拟化 |
