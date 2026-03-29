# Coding Agent 优化计划（全量版）

## 概览

**目标**: 全面提升稳定性、性能和用户体验
**预计工期**: 2-3 天
**优先级**: P0 > P1 > P2

---

## 阶段 1: 稳定性优化（P0）
**工期**: 1 天 | **优先级**: 🔴 最高

### 任务 1.1: API 重试机制
**文件**: `src/coding_agent/providers/openai_compat.py`, `anthropic.py`

**需求**:
- 对 429, 500, 502, 503 错误进行指数退避重试
- 最大重试 3 次，基础延迟 1s
- 其他错误立即抛出

**验收标准**:
```python
# 测试：模拟 429 错误后成功
# 第1次: 429 → 等待 1s + jitter
# 第2次: 429 → 等待 2s + jitter  
# 第3次: 200 → 成功返回
```

**估计**: 45 分钟

---

### 任务 1.2: Token 计数优雅降级
**文件**: `src/coding_agent/tokens.py`

**需求**:
- 捕获 ImportError，自动 fallback 到 ApproximateCounter
- 发出警告日志

**验收标准**:
```python
# 在没有 tiktoken 的环境中
# TiktokenCounter("gpt-4") → 自动使用 ApproximateCounter
# 日志: "tiktoken not available, using ApproximateCounter"
```

**估计**: 15 分钟

---

### 任务 1.3: Provider 连接池
**文件**: `src/coding_agent/providers/openai_compat.py`, `anthropic.py`

**需求**:
- 复用 `httpx.AsyncClient` 或 SDK 的 client
- 支持连接池配置（最大连接数、keep-alive）

**验收标准**:
```python
# 100 次 API 调用只创建 1-5 个 TCP 连接
# 监控: netstat -an | grep ESTABLISHED | wc -l
```

**估计**: 30 分钟

---

## 阶段 2: 体验优化（P1）
**工期**: 1 天 | **优先级**: 🟡 高

### 任务 2.1: KB 索引进度条
**文件**: `src/coding_agent/kb.py`

**需求**:
- 使用 `rich.progress` 显示索引进度
- 显示当前文件、已处理/总数、预估剩余时间

**验收标准**:
```
Indexing codebase...
[████████░░░░░░░░░░░░] 45% • 45/100 files • src/utils/helpers.py • ETA: 12s
```

**估计**: 45 分钟

---

### 任务 2.2: 启动加速（Lazy Import）
**文件**: `src/coding_agent/__main__.py`, `__init__.py`

**需求**:
- 延迟加载重模块（lancedb, tiktoken, numpy）
- 只在首次使用时导入

**验收标准**:
```bash
# 优化前: python -m coding_agent --help  2.5s
# 优化后: python -m coding_agent --help  0.8s
```

**估计**: 1 小时

---

### 任务 2.3: 工具执行时间显示
**文件**: `src/coding_agent/ui/rich_tui.py`

**需求**:
- 在 TUI 中显示每个 tool 的执行耗时
- 超过 1s 的用黄色高亮，超过 5s 用红色

**验收标准**:
```
✓ file_read src/main.py (0.05s)
✓ bash cargo test (12.4s)  ⚠️
```

**估计**: 1 小时

---

### 任务 2.4: 结构化错误提示
**文件**: `src/coding_agent/core/loop.py`, `ui/rich_tui.py`

**需求**:
- 区分用户错误（配置错误、API key 无效）和系统错误
- 用户错误显示友好提示，技术错误显示简略信息 + 日志路径

**验收标准**:
```
# 用户错误
❌ API Key 无效
   请检查 AGENT_API_KEY 环境变量或 --api-key 参数
   文档: https://...

# 系统错误  
❌ 连接超时
   这可能是网络问题。已记录到 ~/.coding-agent/logs/error.log
```

**估计**: 1.5 小时

---

## 阶段 3: 性能优化（P2）
**工期**: 0.5-1 天 | **优先级**: 🟢 中

### 任务 3.1: Token 计数缓存
**文件**: `src/coding_agent/tokens.py`

**需求**:
- 对短文本（<100 chars）使用 LRU Cache
- 缓存大小 10000 条

**验收标准**:
```python
# 相同文本的第二次调用从缓存返回
# 性能提升: 1000 次计数从 50ms → 5ms
```

**估计**: 30 分钟

---

### 任务 3.2: 性能指标收集
**文件**: `src/coding_agent/metrics.py` (新建)

**需求**:
- 收集：工具调用次数、API 延迟、缓存命中率、token 使用量
- 暴露：CLI 命令 `coding-agent stats`

**验收标准**:
```bash
$ coding-agent stats --session last
Session: abc123
Duration: 5m32s
Tools called: 45 (file_read: 30, bash: 10, ...)
Cache hit rate: 87%
API calls: 12 (avg latency: 1.2s)
Tokens used: 45k in / 12k out
```

**估计**: 2 小时

---

### 任务 3.3: 上下文智能摘要（高级）
**文件**: `src/coding_agent/core/context.py`

**需求**:
- 当上下文超过 80% 预算时，触发 LLM 摘要
- 将历史消息摘要为关键点

**验收标准**:
```python
# 原始: 50 条消息 → 12k tokens
# 摘要后: 3 条消息 → 800 tokens
# 保留关键信息: 任务目标、重要决策、待办事项
```

**估计**: 3 小时（需要测试多种摘要策略）

---

## 执行计划

### Day 1: 稳定性（P0）
| 时间 | 任务 | 输出 |
|------|------|------|
| 09:00-09:45 | 1.1 API 重试 | 测试通过 |
| 09:45-10:00 | 1.2 Token 降级 | 测试通过 |
| 10:00-10:30 | 1.3 连接池 | 测试通过 |
| 10:30-11:00 | **整合测试** | 所有 P0 测试通过 |
| 11:00-12:00 | Code Review & 修复 | 代码合并 |

### Day 2: 体验（P1）
| 时间 | 任务 | 输出 |
|------|------|------|
| 09:00-09:45 | 2.1 KB 进度条 | UI 截图 |
| 09:45-10:45 | 2.2 启动加速 | 性能对比数据 |
| 10:45-11:45 | 2.3 工具时间显示 | UI 截图 |
| 11:45-13:15 | 2.4 错误提示 | 错误示例 |
| 13:15-14:00 | **整合测试** | 所有 P1 测试通过 |

### Day 3: 性能（P2）
| 时间 | 任务 | 输出 |
|------|------|------|
| 09:00-09:30 | 3.1 Token 缓存 | 性能 benchmark |
| 09:30-11:30 | 3.2 性能指标 | `coding-agent stats` 可用 |
| 11:30-14:30 | 3.3 上下文摘要（可选）| 摘要质量测试 |
| 14:30-15:00 | **最终测试** | 全量测试通过 |

---

## 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| 连接池导致资源泄漏 | 低 | 高 | 添加连接超时和最大连接数限制 |
| Lazy import 引入循环依赖 | 中 | 中 | 仔细测试导入路径，使用 TYPE_CHECKING |
| 上下文摘要质量差 | 中 | 中 | 先实现简单摘要，迭代优化 |

---

## 成功标准

1. **稳定性**: 网络抖动场景任务成功率 > 95%（当前 ~80%）
2. **启动速度**: CLI 启动 < 1s（当前 ~2.5s）
3. **用户体验**: 长时间操作都有进度反馈
4. **可观测性**: 可以通过命令查看性能指标

---

## 建议的执行方式

**推荐**: 使用 subagent 分阶段执行

1. **阶段 1** → 派 1 个 subagent（稳定性）
2. **阶段 2** → 派 1 个 subagent（体验）  
3. **阶段 3** → 派 1 个 subagent（性能）

每阶段间隔 review，确保质量。

**或者**: 如果你希望更快看到效果，可以只选前 3 个高价值任务：
- 1.1 API 重试
- 2.1 KB 进度条
- 2.2 启动加速

这 3 项合计约 2 小时，但价值最大。

---

**是否开始执行？** 选择：
1. **全量计划** - 按阶段执行所有 11 个任务
2. **精简计划** - 只做前 3 个高价值任务
3. **自定义** - 你指定要哪些任务
