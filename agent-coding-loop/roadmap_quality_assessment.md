# Roadmap 实现质量评估报告

> 基于 2026-03-23 对 `agent-coding-loop` 仓库代码的逐项交叉验证

---

## 总览

| Step | 定位 | 代码实现 | 验收完成 | 质量评级 |
|------|------|---------|---------|---------|
| **Step 1** | 合入已验证改动 | ✅ 全部落地 | ❌ 6 项未收 | ⭐⭐⭐⭐ |
| **Step 2** | 评测精度升级 | ✅ 全部落地 | ❌ 正式 eval 未跑 | ⭐⭐⭐⭐⭐ |
| **Step 3** | Prompt 迭代 | ⚠️ 3.2 部分实现 | ❌ 未开始 | ⭐⭐⭐ |
| **Step 4** | K8S 部署 | ⚠️ 4.3 缺口 | ❌ 未端到端验收 | ⭐⭐⭐ |

---

## Step 1: 合入已验证改动 — ⭐⭐⭐⭐

### 代码质量：优

所有 7 个子项（1.1–1.6+）的代码已合入 [main](file:///Users/kina/Code/Agent/Coding-Agent-Loop/agent-coding-loop/eval/ab/run_ab.py#1272-1415)，实现质量较高：

| 子项 | 代码证据 | 质量评价 |
|------|---------|---------|
| **1.1** targeted-repair-eval | [run_ab.py](file:///Users/kina/Code/Agent/Coding-Agent-Loop/agent-coding-loop/eval/ab/run_ab.py) 完整 repair 追踪链路 (`repair_triggered` / `repair_empty_patch` / `repair_error` / `repair_stage_count`) | ✅ 数据可审计 |
| **1.2** 工具描述改进 | [coder_eino.go:1697–1718](file:///Users/kina/Code/Agent/Coding-Agent-Loop/agent-coding-loop/internal/agent/coder_eino.go#L1697-L1718) 工具约束丰富 | ✅ 有配套单测 |
| **1.3** run_command 描述 | 命令约束在 system prompt 中 ("keep commands minimal and deterministic") | ✅ |
| **1.4** 工具错误结构化 | `wrapStructuredOutputStageError()` 统一封装 | ✅ 错误格式一致 |
| **1.5** Prompt 日志 | `emitPromptStarted/Completed/Error` 三阶段记录到 `tool_calls` 表，key='coder_prompt'/'reviewer_prompt' | ✅ 有集成测试覆盖 |
| **1.6** failure_reason | [store.go](file:///Users/kina/Code/Agent/Coding-Agent-Loop/agent-coding-loop/internal/store/sqlite/store.go) schema 含 `failure_reason TEXT`，自动 migration + backfill | ✅ 有测试覆盖 |
| **1.6+** instrumentation P2 | backfill 逻辑已实现 | ⚠️ Known limitations 未解决（全量重跑 + 逐行子进程） |

### 未封口项

roadmap 列出的 6 个验收项全部未完成：
- `go test ./...` / Python unittest — 未见最新 main 上的执行记录
- `repo_only_006` 真实 run — 未执行
- `state.db` prompt 记录确认 — 未执行
- 5-task targeted eval — 未执行
- worktree 清理 — 未确认

**结论**：代码质量没问题，但**没有正式的验收记录**是最大缺口。如果在 latest main 上跑一次测试 + 简短 eval 就能收掉。

---

## Step 2: 评测精度升级 — ⭐⭐⭐⭐⭐

### 代码质量：优秀（这是整个 roadmap 里做得最好的部分）

**6/6 子项全部已实现**，且实现质量明显高于其他 Step：

| 子项 | 实现方式 | 亮点 |
|------|---------|------|
| **2.0** 解耦三个隐藏耦合 | [split_citation_ref()](file:///Users/kina/Code/Agent/Coding-Agent-Loop/agent-coding-loop/eval/ab/run_ab.py#89-96) / `trap` 字段 / [aggregate_trials()](file:///Users/kina/Code/Agent/Coding-Agent-Loop/agent-coding-loop/eval/ab/run_ab.py#632-661) | 设计考虑到了 2.3–2.5 之间的耦合，**先解耦再实现**，非常工程化 |
| **2.1** Python test_cmd 修复 | [benchmark_tasks.jsonl](file:///Users/kina/Code/Agent/Coding-Agent-Loop/agent-coding-loop/eval/ab/benchmark_tasks.jsonl) 里 `kb_code_004`/`008`/`kb_mixed_003` 的 test_cmd 改为真实 unittest | ✅ |
| **2.2** 规则级断言 | `grep -Fq` 精确字符串检查 + 测试文件存在性检查 | ✅ 覆盖 8/9 个 KB-guided 任务 |
| **2.3** heading 级引用 | `path#heading` 格式全面使用，grader/overlay 兼容 | ✅ 整洁 |
| **2.4** trap 任务 | 2 个 trap 任务 + `trap_kb_search_used` 判罚规则 | ✅ 完整闭环 |
| **2.5** `--trials` + Pass@k | CLI flag + [aggregate_trials()](file:///Users/kina/Code/Agent/Coding-Agent-Loop/agent-coding-loop/eval/ab/run_ab.py#632-661) + report 区分 Pass@k / Pass^k | ✅ paired analysis 正确基于聚合后的 task-level 结果 |
| **2.6** difficulty 分层 | 26 任务全部带 difficulty + [aggregate_metrics_by_difficulty()](file:///Users/kina/Code/Agent/Coding-Agent-Loop/agent-coding-loop/eval/ab/run_ab.py#603-630) | ✅ |

### 特别加分项

1. **[benchmark_tasks.jsonl](file:///Users/kina/Code/Agent/Coding-Agent-Loop/agent-coding-loop/eval/ab/benchmark_tasks.jsonl) 设计精度高**：26 个任务覆盖 4 个 category，10 个 repo_only + 9 个 kb_guided_code + 2 个 kb_only + 3 个 kb_mixed + 2 个 trap，难度分布合理
2. **评测代码有完整测试**：`eval/tests/` 下有 `test_benchmark_tasks.py`、`test_run_ab.py`、`test_evaluate.py`、`test_summarize.py` 等 8 个测试模块
3. **KB 覆盖分析和 kb_search 链路文档**在 roadmap 中有详细记录，说明作者对"评测盲区"有清晰认知

### 未封口项

- 156 次 run 的正式 eval 未执行
- Step 1 基线上的 pass/fail 符合性确认未执行

---

## Step 3: Prompt 迭代 — ⭐⭐⭐

### 当前状态：部分实现，主体未开始

| 子项 | 状态 | 代码验证 |
|------|------|---------|
| **3.1** Reviewer prompt 三层判断 | ❄️ 冻结 | `0c7d289` 归因实验证实中性偏负，正确决策不合入 |
| **3.2** Coder prompt 身份+完成标准 | ⚠️ 部分 | 现有 `coderPrompts()` 有 JSON-only、target file 约束、multi-target patch 要求，但**缺少显式身份句**（"只做最小改动"）和**输出示例 JSON** |
| **3.3** Repair prompt 视野扩展 | 🔒 条件启动 | `repairPrompts()` 当前视野仅有 RepairInput（含 diff + command_output），**未含完整 test output 和原始 goal** |

### 质量评价

- **3.1 的冻结决策是正确的**：归因实验数据支持，避免了盲合
- **3.2 的"部分实现"判断准确**：其中 `coderPrompts()` 已经有 ~20 条约束规则（远超简单的身份句），实际复杂度已经超出了 roadmap 原始定义，但形式上缺了 roadmap 要求的 3 个显式元素
- **3.3 的条件逻辑合理**：repair 触发率极低（1/24），扩展视野的 ROI 不确定

### 风险

roadmap 要求"每个 prompt 改动都有 A/B 数据（26 tasks × --trials 3）"，这意味着 Step 3 的**每次迭代成本 = 156 runs**。如果没有 K8S 批量执行能力（Step 4），Step 3 的迭代速度会很慢。

---

## Step 4: K8S 部署 — ⭐⭐⭐

### 代码质量：可用但不完整

| 子项 | 代码证据 | 质量评价 |
|------|---------|---------|
| **4.1** Dockerfile | 46 行，多阶段构建，含 Go toolchain + Python KB 依赖 | ✅ 生产可用 |
| **4.2** Job 模板 | [job.yaml.tmpl](file:///Users/kina/Code/Agent/Coding-Agent-Loop/agent-coding-loop/eval/k8s/job.yaml.tmpl)：`emptyDir` + `initContainer` git clone + 主容器 `agent-loop run` | ✅ 设计合理 |
| **4.3** 结果收集 | [collect_results.py](file:///Users/kina/Code/Agent/Coding-Agent-Loop/agent-coding-loop/eval/k8s/collect_results.py)：278 行，复用 `run_ab.py` 函数 | ⚠️ 假定 `state.db` 已手动拷回，非自动归档 |
| **4.4** 汇总脚本 | [summarize.py](file:///Users/kina/Code/Agent/Coding-Agent-Loop/agent-coding-loop/eval/k8s/summarize.py)：132 行，调用 `build_report()`/`render_markdown()` | ✅ 有测试 |

### 缺口

1. **`render.go` 不存在**：[README.md](file:///Users/kina/Code/Agent/Coding-Agent-Loop/agent-coding-loop/eval/k8s/README.md#L33) 引用 `go run eval/k8s/render.go` 但文件不存在
2. **自动结果归档缺失**：当前流程需手动 `kubectl cp state.db`，没有 sidecar 或 Job completion hook
3. **`collect_results.py` 无独立测试**：虽然逻辑简单且复用了有测试的 `run_ab.py` 函数，但自身缺少测试
4. **未做端到端验收**：没有证据表明有人在真实 K8S 集群上跑完过完整流程

---

## 整体实现质量判断

### 做得好的

1. **评测基础设施（Step 2）是整个项目最出色的部分**。任务设计、评分逻辑、统计方法都很工程化，且有完整测试覆盖
2. **数据驱动决策**：Step 1 的合入/冻结决策都有实验数据支撑（参见实验数据汇总），不是凭感觉
3. **roadmap 本身的质量很高**：严格依赖排序（Step 1→2→3）、每步有完成标记、Known Limitations 显式列出、隐藏耦合提前识别

### 需要关注的

1. **验收债务**：Step 1 和 Step 2 的代码都写完了，但**没有正式的验收运行记录**。这意味着如果最新 main 上有隐性回归，没人知道
2. **Step 4 的 render.go 缺失**是一个文档与实现不一致的问题，虽然可以用 `sed` 替代，但说明 K8S 链路可能没有被真正跑过
3. **Step 3 的启动条件依赖 Step 2 验收 + Step 4 批量执行**，形成了瓶颈：不跑完 Step 2 验收就无法安全开始 Step 3，不搞定 Step 4 就无法高效迭代 Step 3

### 量化总结

| 指标 | 数值 |
|------|------|
| Roadmap 子项总数 | 17 |
| 代码已实现 | 14 (82%) |
| 部分实现 | 2 (12%) |
| 未实现（冻结/条件） | 1 (6%) |
| 验收已完成 | 0 (0%) |
| 有测试覆盖的模块 | Step 1 (Go 单测) + Step 2 (8 个 Python 测试模块) |
| 缺失文件 | `eval/k8s/render.go` |
