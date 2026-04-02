# Learnings — realtime-bash-toggle

## [2026-04-02] Plan Kickoff

### Architecture Decisions
- sentinel-based session switching 方案：`SWITCH_TO_SHELL = "__SWITCH_TO_SHELL__"` / `SWITCH_TO_CHAT = "__SWITCH_TO_CHAT__"`
- 双 PromptSession 架构保留（不动态 callable）
- `get_input()` 内部 while 循环处理哨兵，对 REPL 透明

### Critical Technical Findings
1. **`eager=True` REQUIRED on Escape** — 否则有 ~500ms 延迟等待 Alt 组合键
2. **Shared KeyBindings + `Condition` filter** — `!` 绑定必须带 `Condition(lambda: not self._shell_mode)`
3. **`event.app.exit(result=X)`** — sets prompt_async() 返回值
4. **Local sentinel constants in test** — 永久 fixtures，无需后续替换为 imports

### File Locations
- 主要修改：`src/coding_agent/cli/input_handler.py`（Task 2）
- REPL：`src/coding_agent/cli/repl.py`（Task 3）
- 清理：`src/coding_agent/cli/bash_executor.py`，`src/coding_agent/cli/commands.py`（Task 4）
- 测试：`tests/cli/test_input_handler.py`（Task 1），`tests/cli/test_repl.py`（Task 3），`tests/cli/test_bash_executor.py`（Task 4）
