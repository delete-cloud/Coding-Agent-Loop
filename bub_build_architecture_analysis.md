# bub.build 架构设计深度分析

## 项目背景

**bub.build** 是一个 hook-first 的 AI agent 框架，核心理念是"与人类并肩工作的 agent 通用形态"。它诞生于群组聊天场景，需要在多用户、多 agent 并发的复杂环境中与真人协作，而非作为独立的个人助手运行。

核心特点：
- 小型核心 (~200行)，基于 **pluggy** 实现 hooks
- 使用 **"tape"** 而非 session 来管理上下文
- CLI 和 Telegram 等通道共享同一个 pipeline
- Skills 是带 frontmatter 验证的 SKILL.md 文件，而非代码模块
- 核心 pipeline 阶段：`resolve_session → load_state → build_prompt → run_model → save_state → render_outbound → dispatch_outbound`

---

## 1. Hook-first 架构的优势

### 1.1 核心思想

Hook-first 架构意味着框架的核心功能完全由可插拔的 hooks 实现，而非传统的继承或装饰器模式。bub.build 使用 **pluggy**（pytest 的插件系统）作为基础，实现了一个完全可扩展的 pipeline。

```python
# 核心 pipeline 定义
resolve_session → load_state → build_prompt → run_model
                                               ↓
          dispatch_outbound ← render_outbound ← save_state
```

### 1.2 为什么选择 Hooks 而非继承/装饰器

| 对比维度 | Hooks (pluggy) | 继承/装饰器 |
|---------|---------------|------------|
| **扩展方式** | 注册插件，无需修改核心代码 | 需要继承基类或包装函数 |
| **多个扩展** | 天然支持多插件链式调用 | 装饰器叠加顺序难以控制 |
| **运行时动态性** | 支持动态发现和加载 | 通常是静态定义 |
| **隔离性** | 插件之间解耦 | 继承链紧密耦合 |
| **测试友好性** | 可单独测试每个 hook | 需要完整的继承链 |

**关键洞察：**
- **Hooks 实现了真正的关注点分离**：每个插件只关心一个特定的 hook 点
- **无需修改框架核心即可扩展**：新的行为通过注册新插件实现
- **执行顺序可控**：通过注册顺序和 `call_first`/`call_many` 语义控制

### 1.3 Builtins 也是可替换的插件

```python
# 注册顺序（决定了优先级）
1. Builtin plugin (builtin)          # 最先注册，最低优先级
2. External entry points (group="bub") # 后注册，可覆盖 builtin

# 执行时反转：后注册的先执行
```

这意味着：
- **框架核心不 privileged 自己的 builtins**
- **所有行为都可被覆盖**：包括 `build_prompt`、`run_model`、`save_state` 等核心功能
- **没有特殊逻辑**：builtin 和第三方插件遵循完全相同的规则

### 1.4 对扩展性的帮助

```python
from bub import hookimpl

class EchoPlugin:
    @hookimpl
    def build_prompt(self, message, session_id, state):
        return f"[echo] {message['content']}"

    @hookimpl
    async def run_model(self, prompt, session_id, state):
        return prompt

# 注册方式（标准 Python entry points）
[project.entry-points."bub"]
echo = "my_package.plugin:EchoPlugin"
```

**扩展优势：**
1. **标准化接口**：所有扩展遵循相同的 `@hookimpl` 模式
2. **独立发布**：插件作为独立包发布，通过 pip 安装
3. **发现机制**：利用 Python 的 entry points 机制自动发现插件
4. **优先级语义**：
   - `call_first`: 返回第一个非 None 值，用于覆盖行为
   - `call_many`: 收集所有返回值，用于聚合数据

### 1.5 在我们的项目中可以如何借鉴

**适用场景：**
- 构建需要高度可扩展的 agent 框架
- 多个团队需要独立开发插件
- 需要支持运行时动态加载/卸载功能

**实现建议：**
```python
# 定义 hook 契约
from pluggy import HookspecMarker

hookspec = HookspecMarker("myagent")

class MyAgentHooks:
    @hookspec
    def process_message(self, message: str, context: dict) -> str:
        """处理输入消息"""

    @hookspec(firstresult=True)
    def generate_response(self, prompt: str) -> str:
        """生成响应（firstresult=True 表示只取第一个非 None 结果）"""

# 实现插件
from pluggy import HookimplMarker

hookimpl = HookimplMarker("myagent")

class LoggingPlugin:
    @hookimpl
    def process_message(self, message, context):
        logger.info(f"Processing: {message}")
        return message
```

---

## 2. Tape-based 上下文管理

### 2.1 核心思想

**Tape** 是 bub.build 中上下文管理的核心抽象，它颠覆了传统的 session/message history 模式。

**传统方式 vs Tape 方式：**

| 传统 Session/Message History | Tape-based 上下文 |
|---------------------------|------------------|
| 累积所有历史消息 | 只追加事实（append-only facts） |
| 需要定期压缩/截断 | 历史永不修改，按需组装 |
| context window 线性增长 | 通过 anchors 重建工作集 |
| 状态继承（lossy） | 状态构建（精确控制） |

### 2.2 "Append-only Facts" 和 "Anchors" 的设计理念

**核心不变式（Invariants）：**
1. **History is append-only, never overwritten**
2. **Derivatives never replace original facts**
3. **Context is constructed, not inherited wholesale**

```
Tape 结构示意：

[Entry 1] → [Entry 2] → [Anchor A] → [Entry 3] → [Entry 4] → [Anchor B] → [Entry 5]
              ↓               ↓
         Immutable        Checkpoint
         Facts            (Phase Transition)
```

**Entry（条目）：**
- 不可变的事实记录
- 通过单调递增 ID 保证顺序可追踪
- 修正通过追加新条目实现，而非删除旧条目

**Anchor（锚点）：**
- 逻辑检查点，标记阶段转换
- 可以携带结构化状态载荷
- 允许从锚点重建，跳过完整扫描

```json
// Anchor 状态契约示例
{
  "phase": "implement",
  "summary": "Discovery complete.",
  "next_steps": ["Run migration", "Integration tests"],
  "source_ids": [128, 130, 131],
  "owner": "agent"
}
```

### 2.3 如何避免 Context Window 爆炸

**策略 1：On-demand Context Assembly**
- 不是继承整个历史，而是按需组装
- View（视图）是任务导向的上下文窗口

**策略 2：Anchor-based Reconstruction**
```
Discovery    handoff    Implement    handoff    Verify
   ↓            ↓           ↓            ↓          ↓
  e1, e2      Anchor A     e4          Anchor B    e6, e7
                          ↓                        ↓
                  从 Anchor A 重建              从 Anchor B 重建
                  跳过 e1, e2                   跳过前面所有
```

**策略 3：Compact/Summary Strategy**
- 在锚点处可以附加摘要信息
- 长历史可以通过摘要压缩

**策略 4：Non-linear Anchor Graph**
- Anchors 可以形成非线性图，而非单条时间线
- 支持 fork/merge 语义
- Memory views 从多个节点组装，由策略指导

### 2.4 在我们的项目中可以如何借鉴

**适用场景：**
- 长对话历史管理
- 多阶段任务（plan → execute → verify）
- 需要审计和回放能力的场景

**实现建议：**
```python
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from datetime import datetime

@dataclass
class TapeEntry:
    id: int
    timestamp: datetime
    content: str
    entry_type: str  # "message", "tool_call", "anchor", "correction"
    metadata: Dict[str, Any]

@dataclass
class Anchor:
    entry_id: int
    phase: str
    summary: str
    state_payload: Dict[str, Any]
    next_steps: List[str]

class Tape:
    def __init__(self):
        self.entries: List[TapeEntry] = []
        self.anchors: List[Anchor] = []
        self._next_id = 1
    
    def append(self, content: str, entry_type: str, metadata: dict = None) -> TapeEntry:
        entry = TapeEntry(
            id=self._next_id,
            timestamp=datetime.now(),
            content=content,
            entry_type=entry_type,
            metadata=metadata or {}
        )
        self.entries.append(entry)
        self._next_id += 1
        return entry
    
    def create_anchor(self, phase: str, summary: str, state: dict, next_steps: List[str]):
        entry = self.append(f"ANCHOR: {phase}", "anchor", {"summary": summary})
        anchor = Anchor(
            entry_id=entry.id,
            phase=phase,
            summary=summary,
            state_payload=state,
            next_steps=next_steps
        )
        self.anchors.append(anchor)
        return anchor
    
    def get_view_since_anchor(self, anchor_id: int) -> List[TapeEntry]:
        """获取从指定锚点之后的所有条目"""
        anchor_idx = next(
            (i for i, a in enumerate(self.anchors) if a.entry_id == anchor_id),
            None
        )
        if anchor_idx is None:
            return self.entries
        
        anchor_entry_id = self.anchors[anchor_idx].entry_id
        return [e for e in self.entries if e.id >= anchor_entry_id]
```

---

## 3. 统一 Pipeline 设计

### 3.1 核心思想

CLI 和 Telegram（以及任何其他通道）共享同一个 `process_inbound()` 代码路径，hooks 不知道自己在哪个通道中运行。

```python
# 统一入口
BubFramework.process_inbound(message)

# CLI 和 Telegram 都调用同一个方法
```

### 3.2 process_inbound() 的设计

```python
# Turn 生命周期
1. resolve_session(message)          # 确定 session
2. load_state(message, session_id)   # 加载状态
3. build_prompt(message, session_id, state)  # 构建 prompt
4. run_model(prompt, session_id, state)      # 运行模型
5. save_state(...)                   # 保存状态（finally 块中始终执行）
6. render_outbound(...)              # 渲染输出
7. dispatch_outbound(message)        # 分发输出
```

**关键设计决策：**
- **Envelope 弱类型**：使用 `Any` + accessor helpers，保持灵活性
- **状态无全局强制 schema**：跨插件的 `state` 没有强制结构
- **错误隔离**：`on_error` hook 是 observer-safe，一个失败不阻塞其他

### 3.3 这对测试和维护的好处

**测试友好：**
```python
# 可以轻松模拟任何通道的输入
message = {
    "content": "hello",
    "channel": "test",  # 可以是 cli, telegram, test
    "session_id": "test-session"
}
result = framework.process_inbound(message)
```

**维护优势：**
1. **单点修改**：修改 pipeline 逻辑只需改一处
2. **通道无关的 bug 修复**：修复一个通道的问题等于修复所有通道
3. **一致的行为保证**：所有通道的行为保证一致
4. **易于添加新通道**：只需实现适配器，无需修改核心逻辑

### 3.4 在我们的项目中可以如何借鉴

**适用场景：**
- 多通道部署（Web、移动端、第三方 IM）
- 需要统一行为保证的系统
- 测试驱动开发

**实现建议：**
```python
from abc import ABC, abstractmethod
from typing import Any, Dict

class ChannelAdapter(ABC):
    @abstractmethod
    def normalize_input(self, raw_input: Any) -> Dict:
        """将通道特定输入转换为统一格式"""
        pass
    
    @abstractmethod
    def render_output(self, output: Dict) -> Any:
        """将统一输出转换为通道特定格式"""
        pass
    
    @abstractmethod
    def dispatch(self, rendered_output: Any) -> None:
        """发送输出到通道"""
        pass

class UnifiedPipeline:
    def __init__(self):
        self.channels: Dict[str, ChannelAdapter] = {}
    
    def register_channel(self, name: str, adapter: ChannelAdapter):
        self.channels[name] = adapter
    
    async def process(self, channel_name: str, raw_input: Any):
        adapter = self.channels[channel_name]
        
        # 统一处理流程
        message = adapter.normalize_input(raw_input)
        session_id = self.resolve_session(message)
        state = await self.load_state(session_id)
        prompt = await self.build_prompt(message, state)
        response = await self.run_model(prompt)
        await self.save_state(session_id, response)
        
        # 通道特定输出
        output = {"response": response, "session_id": session_id}
        rendered = adapter.render_output(output)
        await adapter.dispatch(rendered)
        
        return response
```

---

## 4. Skill 即文档

### 4.1 核心思想

Skills 不是代码模块，而是 **SKILL.md** 文件——带验证 frontmatter 的 Markdown 文档。

**传统方式 vs Skill 即文档：**

| 传统代码模块 | Skill 即文档 |
|-----------|-------------|
| 需要注册/导入代码 | 文件系统即注册表 |
| 魔法装饰器 (@skill) | 标准化 frontmatter |
| 代码即文档 | 文档即配置 |
| 技能逻辑在代码中 | 技能逻辑在 prompt 中 |

### 4.2 Validated Frontmatter 的设计

**文件结构：**
```
skills/
└── my-skill/
    └── SKILL.md
```

**SKILL.md 格式：**
```markdown
---
name: my-skill
description: Brief description of what this skill does
metadata:
  version: "1.0"
  author: "team-a"
---

# Skill Instructions

Detailed instructions for the agent on how to use this skill...
```

**验证规则：**
- `SKILL.md` 必须以 YAML frontmatter 开头（`--- ... ---`）
- frontmatter 必须包含非空的 `name` 和 `description`
- 目录名必须与 frontmatter 的 `name` 完全匹配
- `name` 必须符合 `^[a-z0-9]+(?:-[a-z0-9]+)*$`（kebab-case）
- `name` 长度 ≤ 64
- `description` 长度 ≤ 1024
- `metadata`（如有）必须是 `string -> string` 的 map

### 4.3 这对技能发现和作者体验的影响

**技能发现（Discovery）：**
```python
# 发现顺序（优先级）
1. project: .agents/skills/     # 项目级最高优先级
2. user:    ~/.agents/skills/   # 用户级
3. builtin: src/skills/         # 内置最低优先级

# 同名 skill 按优先级覆盖
```

**作者体验：**
1. **无需编程**：编写 Markdown 即可创建 skill
2. **即时生效**：保存文件即可使用，无需重启
3. **版本友好**：diff-friendly，易于版本控制
4. **渐进式复杂度**：简单 skill 只需 frontmatter + 简单说明

**Skill 加载时的渐进式披露（Progressive Disclosure）：**
1. 系统只加载 frontmatter（name, description）到可用技能列表
2. 当 agent 决定使用某 skill 时，才加载完整的 SKILL.md 内容
3. 避免 context window 被未使用的 skills 填满

### 4.4 在我们的项目中可以如何借鉴

**适用场景：**
- 非技术用户需要创建/修改 agent 行为
- 需要频繁迭代 prompt 的场景
- 多团队协作，需要清晰的技能边界

**实现建议：**
```python
import yaml
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

@dataclass
class Skill:
    name: str
    description: str
    content: str  # Markdown body (without frontmatter)
    metadata: dict
    path: Path

class SkillRegistry:
    def __init__(self, search_paths: list):
        self.search_paths = search_paths
        self.skills: dict[str, Skill] = {}
    
    def parse_frontmatter(self, content: str) -> tuple:
        """解析 frontmatter，返回 (metadata, body)"""
        pattern = r'^---\r?\n(.*?)\r?\n---\r?\n?(.*)$'
        match = re.match(pattern, content, re.DOTALL)
        if not match:
            raise ValueError("No frontmatter found")
        
        metadata = yaml.safe_load(match.group(1))
        body = match.group(2).strip()
        return metadata, body
    
    def validate_skill(self, name: str, metadata: dict) -> bool:
        """验证 skill 元数据"""
        if not metadata.get('name') or not metadata.get('description'):
            return False
        if metadata['name'] != name:
            return False
        if not re.match(r'^[a-z0-9]+(?:-[a-z0-9]+)*$', name):
            return False
        if len(name) > 64:
            return False
        if len(metadata.get('description', '')) > 1024:
            return False
        return True
    
    def discover(self):
        """从所有搜索路径发现 skills"""
        seen = set()
        
        for path in self.search_paths:
            if not path.exists():
                continue
            
            for skill_dir in path.iterdir():
                if not skill_dir.is_dir():
                    continue
                
                skill_file = skill_dir / 'SKILL.md'
                if not skill_file.exists():
                    continue
                
                name = skill_dir.name
                if name in seen:  # 高优先级已加载
                    continue
                
                try:
                    content = skill_file.read_text()
                    metadata, body = self.parse_frontmatter(content)
                    
                    if self.validate_skill(name, metadata):
                        self.skills[name] = Skill(
                            name=name,
                            description=metadata['description'],
                            content=body,
                            metadata=metadata.get('metadata', {}),
                            path=skill_dir
                        )
                        seen.add(name)
                except Exception as e:
                    print(f"Failed to load skill {name}: {e}")
    
    def get_skill(self, name: str) -> Optional[Skill]:
        return self.skills.get(name)
    
    def list_skills(self) -> list:
        """返回所有可用 skills 的元数据（不含完整内容）"""
        return [
            {"name": s.name, "description": s.description}
            for s in self.skills.values()
        ]
```

---

## 5. 其他值得借鉴的细节

### 5.1 AGENTS.md 自动追加到 System Prompt

**设计：**
- 如果工作空间存在 `AGENTS.md` 文件，自动将其内容追加到 system prompt
- 项目特定的上下文/规则可以放在这里

**价值：**
- 项目级 agent 配置标准化
- 无需修改代码即可定制 agent 行为
- 与版本控制集成

**借鉴应用：**
```python
def build_system_prompt(base_prompt: str, workspace_path: Path) -> str:
    agents_md = workspace_path / 'AGENTS.md'
    if agents_md.exists():
        custom_context = agents_md.read_text()
        return f"{base_prompt}\n\n## Project Context\n{custom_context}"
    return base_prompt
```

### 5.2 内部命令模式（,help, ,skill, ,fs.read）

**设计：**
- 以 `,` 开头的输入进入内部命令模式
- 绕过模型推理，直接执行内置命令
- 示例：`,help`, `,skill name=my-skill`, `,fs.read path=README.md`

**价值：**
- 紧急/管理操作不消耗模型调用
- 用户可以快速获取帮助或检查状态
- 命令与对话清晰分离

**借鉴应用：**
```python
class CommandRouter:
    def __init__(self):
        self.commands: dict[str, callable] = {}
    
    def register(self, name: str, handler: callable):
        self.commands[name] = handler
    
    def route(self, input_text: str) -> Optional[str]:
        if not input_text.startswith(','):
            return None  # 不是命令，进入模型处理
        
        parts = input_text[1:].split()
        cmd_name = parts[0]
        args = parts[1:]
        
        if cmd_name in self.commands:
            return self.commands[cmd_name](*args)
        return f"Unknown command: {cmd_name}"

# 使用
router = CommandRouter()
router.register('help', lambda: show_help())
router.register('skill', lambda name: load_skill(name))

result = router.route(user_input)
if result is not None:
    return result  # 命令直接响应
# 否则进入模型推理流程
```

### 5.3 配置设计（BUB_MODEL, BUB_MAX_STEPS 等）

**设计：**
| 变量 | 默认值 | 描述 |
|-----|-------|------|
| `BUB_MODEL` | `openrouter:qwen/qwen3-coder-next` | 模型标识符 |
| `BUB_API_KEY` | — | Provider key |
| `BUB_API_BASE` | — | 自定义 provider endpoint |
| `BUB_API_FORMAT` | `completion` | `completion`, `responses`, 或 `messages` |
| `BUB_MAX_STEPS` | `50` | 最大工具使用循环迭代次数 |
| `BUB_MAX_TOKENS` | `1024` | 每次模型调用的最大 tokens |
| `BUB_MODEL_TIMEOUT_SECONDS` | — | 模型调用超时 |

**价值：**
- 统一的环境变量命名规范
- 合理的默认值降低配置负担
- 支持多种 API 格式（completion/responses/messages）

### 5.4 模型的多种 API Format 支持

**设计：**
通过 `BUB_API_FORMAT` 支持：
- `completion`: 传统 completion API
- `responses`: 新的 responses API
- `messages`: messages API

**价值：**
- 不绑定到特定模型 provider
- 可以轻松切换模型而不修改代码
- 适配不同 provider 的 API 差异

**借鉴应用：**
```python
from enum import Enum
from typing import Protocol

class APIFormat(Enum):
    COMPLETION = "completion"
    RESPONSES = "responses"
    MESSAGES = "messages"

class ModelClient(Protocol):
    async def complete(self, prompt: str) -> str:
        ...

class CompletionClient:
    async def complete(self, prompt: str) -> str:
        # 使用 /v1/completions
        pass

class MessagesClient:
    async def complete(self, prompt: str) -> str:
        # 使用 /v1/chat/completions
        pass

def create_client(format: APIFormat, config: dict) -> ModelClient:
    if format == APIFormat.COMPLETION:
        return CompletionClient(config)
    elif format == APIFormat.MESSAGES:
        return MessagesClient(config)
    # ...
```

---

## 总结：关键设计原则

1. **Hook-first**：所有行为都是插件，包括 builtins
2. **Tape-based**：append-only facts，按需组装上下文
3. **Unified Pipeline**：所有通道共享同一代码路径
4. **Skill as Document**：Markdown + frontmatter 即技能
5. **Progressive Disclosure**：按需加载，避免 context 膨胀
6. **Environment-driven**：通过环境变量配置，而非代码修改

这些设计共同构成了一个**高度可扩展、易于维护、作者友好**的 agent 框架，特别适合需要长期运行、多通道部署、团队协作的场景。
