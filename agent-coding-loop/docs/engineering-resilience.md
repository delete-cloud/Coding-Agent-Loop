# Coding Agent Loop · LLM 输出不稳定的工程化容错

本文总结 `agent-coding-loop` 在面对 LLM 输出不稳定时采用的主要工程化容错策略，重点覆盖协议兼容、结构化输出解析、patch 落地和循环控制。

---

## 总览

LLM 在 Agent 场景下的不稳定性主要集中在四个层面，本项目分别为每个层面提供了对应的处理机制：

| 问题层面 | 具体表现 | 解决方案 | 核心代码 |
|---------|---------|---------|---------|
| 协议层 | tool-call 消息缺少必填 content | `compatToolCallingModel` 消息规范化 | `internal/agent/client_eino.go` |
| 格式层 | JSON 被 code fence 包裹、字段类型漂移 | `extractJSON` + 容错解码 + completion fallback | `internal/agent/coder_eino.go` / `internal/agent/client_eino.go` |
| 产物层 | patch hunk 行数错误、路径带仓库前缀 | patch 清洗、路径重写、受控 fallback | `internal/git/git.go` |
| 行为层 | 重复生成相同 patch / 执行相同命令 | `observeDoom` 死循环检测 | `internal/loop/engine_eino.go` |

---

## 1. Tool-Call 协议兼容

**文件**：`internal/agent/client_eino.go`

### 问题

部分 OpenAI-compatible 模型在 tool-call 场景下会返回空 `content`，但下游框架或协议实现要求该字段非空，导致 ReAct 流程中断。

### 解决方案

创建模型时，对存在该兼容性问题的 provider 包装 `compatToolCallingModel`，统一修正消息结构；同时使用 `retryToolCallingModel` 为所有模型增加可控重试层。

```go
if isDeepSeekBaseURL(c.BaseURL) {
    out = compatToolCallingModel{inner: out}
}
out = retryToolCallingModel{inner: out}
```

`normalizeChatMessages` 会扫描消息列表，对以下情况补入占位内容：

- Assistant 消息存在 `ToolCalls` 但 `Content` 为空
- Tool 消息 `Content` 为空

```go
func normalizeChatMessages(in []*schema.Message) []*schema.Message {
    for _, msg := range in {
        if msg.Role == schema.Assistant && len(msg.ToolCalls) > 0 && strings.TrimSpace(msg.Content) == "" {
            needsContent = true
        }
        if msg.Role == schema.Tool && strings.TrimSpace(msg.Content) == "" {
            needsContent = true
        }
        if needsContent {
            cp := *msg
            cp.Content = " "
            out = append(out, &cp)
        }
    }
}
```

### 重试机制

`retryToolCallingModel` 对瞬态错误做最多 3 次重试，覆盖 TLS 抖动、连接重置、网关错误和 EOF 类失败，并通过 `ctx.Done()` 支持中断。

---

## 2. JSON 解码容错

**文件**：`internal/agent/coder_eino.go`、`internal/agent/client_eino.go`

### 问题

LLM 返回的结构化结果通常存在以下不稳定性：

- JSON 被 ```json 代码块包裹
- 字段类型不稳定，例如 `commands` 可能是 `[]string` 或单个 `string`
- tool-call 路径整体失败，无法直接得到可解析 JSON

### 解决方案

#### 第一层：`extractJSON`

先剥离 markdown code fence，再尝试截取首个平衡 JSON 值。

```go
func extractJSON(content string) string {
    trimmed = strings.TrimPrefix(trimmed, "```json")
    trimmed = strings.TrimPrefix(trimmed, "```")
    trimmed = strings.TrimSuffix(trimmed, "```")
    return strings.TrimSpace(trimmed)
}
```

#### 第二层：容错解码

将原始内容先解码到 `map[string]any`，再逐字段兼容多种形态。

```go
if c, ok := m["commands"]; ok {
    b, _ := json.Marshal(c)
    var items []string
    if err := json.Unmarshal(b, &items); err == nil {
        out.Commands = items
    } else {
        var s string
        if err2 := json.Unmarshal(b, &s); err2 == nil {
            out.Commands = []string{s}
        }
    }
}
```

#### 第三层：completion fallback

当 Eino ReAct tool-call 路径失败时，降级到直接 completion 路径，尽量保留可用输出。

```go
func (c *Coder) Generate(ctx context.Context, in CoderInput) (CoderOutput, error) {
    out, err := c.generateWithEino(ctx, in)
    if err == nil {
        return out, nil
    }
    fallback, fallbackErr := c.generateWithClient(ctx, in)
    fallback.Notes += "\nEino tool-call path failed, fallback completion used."
    return fallback, nil
}
```

---

## 3. Patch 清洗与落地

**文件**：`internal/git/git.go`

### 问题

LLM 生成的 unified diff 常见问题包括：

1. patch 被 ```diff 包裹
2. hunk header 中 old/new count 与实际内容不一致
3. patch 路径带仓库目录前缀，和 `git apply` 的视角不一致
4. `git apply` 失败后缺少可控 fallback

### 解决方案

patch 在真正执行前会经过清洗与标准化：

```text
patch -> normalizeUnifiedDiff
      -> rewriteUnifiedDiffPathsForGitRoot
      -> git apply / git apply --3way
      -> add-only / controlled rewrite fallback
```

#### `fixHunkCounts`

遍历 hunk 中的上下文、删除和新增行，按实际内容重算 old/new count，覆盖模型生成的错误 header。

```go
switch l[0] {
case ' ':
    oldCount++; newCount++
case '-':
    oldCount++
case '+':
    newCount++
}
```

#### 路径重写

在 subdir repo 或 worktree 场景下，将 repo 相对路径改写成 git root 视角路径，避免 `git apply` 对不上目标文件。

```go
func rewriteDiffPathTokenForGitRoot(tok string, prefix string, repoBase string) string {
    switch {
    case strings.HasPrefix(tok, "a/"):
        return "a/" + ensureGitRootRelPath(strings.TrimPrefix(tok, "a/"), prefix, repoBase)
    case strings.HasPrefix(tok, "b/"):
        return "b/" + ensureGitRootRelPath(strings.TrimPrefix(tok, "b/"), prefix, repoBase)
    }
    return ensureGitRootRelPath(tok, prefix, repoBase)
}
```

#### 受控 fallback

当 `git apply` 与 `git apply --3way` 都失败时，再进入受控 fallback：

- `applyAddOnlyPatchFallback`：处理纯新增型 patch
- `applyControlledRewritePatch`：对可安全重写的文件块进行受控替换

这部分逻辑限定了 patch 大小、文件数和路径范围，避免无边界地直接改写文件。

---

## 4. 死循环检测

**文件**：`internal/loop/engine_eino.go`

### 问题

Agent 可能反复生成相同 patch 或重复执行相同命令，持续消耗迭代次数而不产生有效进展。

### 解决方案

使用 `observeDoom` 在 `loopSession` 中记录最近一次工具调用和输入；当同一调用连续出现达到阈值时，直接将运行标记为 `blocked`。

```go
func (e *Engine) observeDoom(st *loopSession, tool string, input any) bool {
    serialized := fmt.Sprintf("%v", input)
    if st.DoomLastTool == tool && st.DoomLastInput == serialized {
        st.DoomCount++
    } else {
        st.DoomLastTool = tool
        st.DoomLastInput = serialized
        st.DoomCount = 1
    }
    return st.DoomCount >= e.doomThresh
}
```

该状态会随 checkpoint 持久化，因此在 resume 后仍然有效。

### 触发点

当前在两个关键位置启用检测：

- `git_apply` 前：连续相同 patch
- `run_command` 前：连续相同命令

触发后运行状态设为 `RunStatusBlocked`，并通过分支路由终止当前循环。

---

## 5. 命令执行安全边界

**文件**：`internal/tools/command.go`、`internal/loop/engine_eino.go`

### 工具名混入过滤

模型有时会把 `repo_read`、`repo_search` 等工具名误写进 shell commands。`sanitizeShellCommands` 会在执行前过滤这些条目，避免把工具调用当成真实 shell 命令执行。

```go
func sanitizeShellCommands(in []string) []string {
    toolNames := map[string]struct{}{
        "repo_list": {}, "repo_read": {}, "repo_search": {},
        "git_diff": {}, "list_skills": {}, "view_skill": {}, "run_command": {},
    }
    for _, raw := range in {
        fields := strings.Fields(cmd)
        if _, ok := toolNames[strings.ToLower(fields[0])]; ok {
            continue
        }
        out = append(out, cmd)
    }
}
```

### 危险命令拦截

`IsDangerousCommand` 会拦截明显破坏性的命令，例如：

```go
blocked := []string{"rm -rf", "git reset --hard", "git checkout --", ":(){:|:&};:", "mkfs", "dd if="}
```

### Reviewer 只读模式

Reviewer 仅注入只读工具，且 `Runner` 运行在 `readOnly: true` 模式下，阻止 `git commit`、`git push`、`sed -i` 等写操作，保证评审阶段的执行边界。
