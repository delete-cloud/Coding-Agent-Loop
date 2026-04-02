# Real-time Bash Mode Toggle (Claude Code Style)

## TL;DR

> **Quick Summary**: 实现按键级别的 `!` bash mode 切换 —— 在空输入时按下 `!` 即时切换到 shell prompt，无需按 Enter。退出通过 Escape 或空 buffer 时 Backspace。参考 Claude Code / OpenCode 的 UX。
>
> **Deliverables**:
> - InputHandler 中的 `!` 键绑定拦截 + 哨兵循环机制
> - Escape (`eager=True`) 和 Backspace-on-empty 退出绑定
> - REPL 重构：移除旧 Enter-based toggle，改用 InputHandler 内部模式管理
> - 清理死代码 (`is_bash_mode_toggle()` 等)
> - 完整 TDD 测试覆盖
>
> **Estimated Effort**: Medium (4 tasks, ~2-3 hours)
> **Parallel Execution**: YES - 2 waves
> **Critical Path**: Task 1 → Task 2 → Task 3 → Task 4 → F1-F4

---

## Context

### Original Request
用户要求实现 Claude Code 风格的实时 bash mode 切换：输入 `!` 作为空白输入的首字符时，即时切换到 bash mode（按键级别，无需 Enter），并有视觉提示。

### Interview Summary
**Key Discussions**:
- 参考了 Claude Code, OpenCode, Kimi CLI 三个工具的实现
- 选择 sentinel-based session switching 方案
- 保留双 PromptSession 架构（不用动态 prompt callable）
- TDD 方法：先写失败测试，再实现

**Research Findings**:
- Claude Code: `!` 首字符即时切换，`!` 被消费，prompt 变 `$`
- OpenCode: `event.key === "!" && cursorPosition === 0` + `preventDefault()`
- Kimi CLI: 用 Ctrl-X（不采用）
- prompt_toolkit: `event.app.exit(result=sentinel)` 可作为 prompt_async 返回值
- **关键发现**: Escape 绑定必须用 `eager=True`，否则有 ~0.5s 延迟（等待 Alt 组合键）

### Metis Review
**Identified Gaps** (addressed):
- `eager=True` on Escape: 已纳入必要条件
- Shared KeyBindings 需要 `Condition` filter: `!` 绑定必须只在 chat mode 且 buffer 空时触发
- One-off `!command` 行为变化: 采用自然行为 —— `!` 切到 shell，然后输入命令
- REPL-InputHandler 接口: 采用 Option 2 —— `handler.shell_mode` property
- `is_bash_mode_toggle()` 清理: 移除死代码

---

## Work Objectives

### Core Objective
将 bash mode toggle 从 "输入 `!` + 按 Enter" 升级为 "按下 `!` 键即时切换"，实现与 Claude Code 一致的 UX。

### Concrete Deliverables
- `input_handler.py`: `!`/Escape/Backspace 键绑定 + 哨兵循环 + `shell_mode` property
- `repl.py`: 移除 `_shell_mode` 状态管理，改用 `handler.shell_mode`
- `bash_executor.py`: 移除 `is_bash_mode_toggle()` 函数
- 测试文件: 新增 ~10 个测试，更新 ~6 个已有测试

### Definition of Done
- [ ] `uv run pytest tests/cli/ -v` — 所有测试通过（含新增测试）
- [ ] `uv run pytest tests/ui/ -v` — 101 个测试全部通过（无回归）
- [ ] `basedpyright` 对 `input_handler.py` 和 `repl.py` 报 0 error

### Must Have
- `!` 在空 buffer 上即时切换到 shell mode（按键级别，不需 Enter）
- `!` 字符被消费，不进入 buffer
- Shell prompt 视觉立刻变化（使用现有 `build_prompt(shell_mode=True)`）
- Escape 即时退回 chat mode（`eager=True`，无延迟）
- 空 buffer 时 Backspace 退回 chat mode
- 非空 buffer 时 `!`/Escape/Backspace 保持正常行为
- 双 PromptSession 分离历史
- 所有现有测试不回归

### Must NOT Have (Guardrails)
- **DO NOT** 触碰 `BashExecutor.execute()` 或其测试
- **DO NOT** 修改 prompt 样式 (PROMPT_STYLE, build_prompt 格式化)
- **DO NOT** 改变 SlashCommandCompleter 行为
- **DO NOT** 添加新依赖
- **DO NOT** 改变 Ctrl-C 或 Ctrl-D 行为
- **DO NOT** 修改 `_process_message()` 或 agent pipeline 代码
- **DO NOT** 创建新的**源代码或测试文件**（所有改动在现有文件中；`.sisyphus/evidence/` 下的证据文件除外）
- **DO NOT** 添加 logging/debug prints
- **DO NOT** 重写未修改方法的 docstring
- **DO NOT** 重构无关代码（Ctrl-C double-tap, continuation prompt 等）
- **DO NOT** 添加 toggle 行为的配置选项

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: TDD (RED → GREEN → REFACTOR)
- **Framework**: pytest + pytest-asyncio
- **Commands**: `uv run pytest tests/cli/ -v`, `uv run pytest tests/ui/ -v`

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **Unit Tests**: pytest 直接验证键绑定行为、哨兵循环、模式切换
- **LSP Diagnostics**: basedpyright 验证类型正确性
- **Integration**: REPL 级别的 mock 测试验证完整流程

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Sequential — TDD Red-Green):
├── Task 1: [RED] Write failing tests for keystroke-level toggle [deep]
└── Task 2: [GREEN] Implement InputHandler keystroke bindings + sentinel loop [deep]

Wave 2 (Sequential — Integration + Cleanup):
├── Task 3: Refactor REPL to use InputHandler-managed shell mode [deep]
└── Task 4: Clean up dead code + update help text [quick]

Wave FINAL (After ALL tasks — 4 parallel reviews):
├── F1: Plan compliance audit (oracle)
├── F2: Code quality review (unspecified-high)
├── F3: Real manual QA (unspecified-high)
└── F4: Scope fidelity check (deep)
→ Present results → Get explicit user okay

Critical Path: Task 1 → Task 2 → Task 3 → Task 4 → F1-F4 → user okay
```

### Dependency Matrix

| Task | Depends On | Blocks |
|------|-----------|--------|
| 1    | —         | 2      |
| 2    | 1         | 3      |
| 3    | 2         | 4      |
| 4    | 3         | F1-F4  |

### Agent Dispatch Summary

- **Wave 1**: 2 tasks — T1 → `deep`, T2 → `deep`
- **Wave 2**: 2 tasks — T3 → `deep`, T4 → `quick`
- **FINAL**: 4 tasks — F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

- [x] 1. [RED] Write Failing Tests for Keystroke-Level Bash Mode Toggle

  **What to do**:
  - Add new test class `TestKeystrokeBashToggle` in `tests/cli/test_input_handler.py` with the following tests:
    1. `test_bang_on_empty_buffer_exits_with_shell_sentinel` — Create InputHandler, get the `!` key binding, simulate event with empty buffer + cursor at 0, assert `event.app.exit()` called with `SWITCH_TO_SHELL` sentinel
    2. `test_bang_on_nonempty_buffer_inserts_bang` — Buffer has "hello", simulate `!`, assert buffer text becomes "hello!" (normal insert)
    3. `test_bang_in_shell_mode_inserts_bang` — Set `_shell_mode=True`, simulate `!` on empty buffer, assert `!` inserted (not intercepted)
    4. `test_escape_in_shell_mode_empty_buffer_exits_with_chat_sentinel` — Set `_shell_mode=True`, empty buffer, simulate Escape, assert `event.app.exit()` called with `SWITCH_TO_CHAT` sentinel
    5. `test_escape_in_shell_mode_nonempty_buffer_no_exit` — Set `_shell_mode=True`, buffer="ls", simulate Escape, assert exit NOT called
    6. `test_escape_in_chat_mode_not_active` — Verify Escape binding has filter that excludes chat mode
    7. `test_backspace_in_shell_mode_empty_buffer_exits_to_chat` — Set `_shell_mode=True`, empty buffer, simulate Backspace, assert `event.app.exit()` with `SWITCH_TO_CHAT`
    8. `test_backspace_in_shell_mode_nonempty_buffer_deletes_char` — Buffer="ls", simulate Backspace, assert default behavior (not intercepted)
  - Add new test class `TestGetInputSentinelLoop` in same file:
    9. `test_get_input_switches_to_shell_on_sentinel` — Mock chat prompt_async to return `SWITCH_TO_SHELL`, then mock shell prompt_async to return "ls -la", assert `get_input()` returns "ls -la"
    10. `test_get_input_switches_back_to_chat_on_sentinel` — Mock chat returns `SWITCH_TO_SHELL`, shell returns `SWITCH_TO_CHAT`, second chat returns "hello", assert returns "hello"
    11. `test_get_input_never_returns_sentinels` — Any sequence of sentinels eventually resolves to real text
    12. `test_shell_mode_property_exposed` — After sentinel switch, `handler.shell_mode` is True; after switch back, False
    13. `test_rapid_toggle_bang_then_escape` — Mock: chat→SWITCH_TO_SHELL→shell→SWITCH_TO_CHAT→chat→"hi", returns "hi"
  - Define sentinel constants locally in the test file to avoid import errors (since they don't exist in production code yet):
    ```python
    # These will be imported from input_handler once Task 2 implements them.
    # For RED phase, define locally so the test module compiles.
    SWITCH_TO_SHELL = "__SWITCH_TO_SHELL__"
    SWITCH_TO_CHAT = "__SWITCH_TO_CHAT__"
    ```
  - The sentinel-loop tests (TestGetInputSentinelLoop) that mock `prompt_async` to return these strings will fail because `get_input()` doesn't have the sentinel loop yet — it will return the sentinel string as-is instead of looping.
  - The binding tests (TestKeystrokeBashToggle) will fail because the `!`, Escape, and Backspace bindings don't exist yet — there's no binding to extract.
  - **These local constants are permanent** — they mirror the production values and serve as test-level fixtures. No need to replace with imports later.
  - **These tests are expected to FAIL/ERROR** — the bindings and sentinel loop don't exist yet

  **Must NOT do**:
  - DO NOT implement any production code
  - DO NOT modify input_handler.py source
  - DO NOT modify existing tests (only add new ones)

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: TDD red phase requires understanding prompt_toolkit's event model for accurate mock design
  - **Skills**: [`test-driven-development`]
    - `test-driven-development`: Core TDD workflow — writing failing tests before implementation

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 1 (sequential with Task 2)
  - **Blocks**: Task 2
  - **Blocked By**: None (can start immediately)

  **References** (CRITICAL):

  **Pattern References**:
  - `tests/cli/test_input_handler.py:1-71` — Existing test patterns: handler creation, prompt building, completer testing
  - `tests/cli/test_repl.py:12-16` — `_get_key_binding()` helper function for extracting bindings by key
  - `tests/cli/test_repl.py:184-226` — Ctrl-C binding test pattern with DummyApp/DummyBuffer mocks

  **API/Type References**:
  - `src/coding_agent/cli/input_handler.py:48-74` — InputHandler.__init__: dual session setup, binding creation
  - `src/coding_agent/cli/input_handler.py:86-116` — `_setup_bindings()`: existing binding patterns (Ctrl-C, Ctrl-D, Enter)
  - `src/coding_agent/cli/input_handler.py:144-158` — `get_input()`: current signature and flow

  **External References**:
  - prompt_toolkit KeyBindings: `event.app.exit(result=...)` sets prompt_async return value
  - prompt_toolkit Condition: `from prompt_toolkit.filters import Condition` for binding filters

  **WHY Each Reference Matters**:
  - `test_repl.py:12-16`: The `_get_key_binding()` helper extracts bindings by key tuple — reuse this pattern for `!`, Escape, Backspace bindings
  - `test_repl.py:184-226`: Shows how to create DummyApp/DummyBuffer mocks, simulate event, and assert exit/reset calls — this is the exact pattern needed for new binding tests
  - `input_handler.py:86-116`: Shows the existing binding registration style — new bindings must follow same `@self.bindings.add()` pattern

  **Acceptance Criteria**:

  - [ ] New test file compiles without import errors (sentinel constants defined locally)
  - [ ] Running `uv run pytest tests/cli/test_input_handler.py -v` shows new tests as FAILED/ERROR (expected — no implementation yet)
  - [ ] Existing 8 tests in the file still PASS (no import-time failures)
  - [ ] 13 new tests cover: `!` binding (3), Escape binding (3), Backspace binding (2), sentinel loop (5)

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: New tests exist and are properly failing
    Tool: Bash
    Preconditions: No changes to input_handler.py source
    Steps:
      1. Run `uv run pytest tests/cli/test_input_handler.py -v 2>&1`
      2. Count lines matching "PASSED" — should be >= 8 (existing tests)
      3. Count lines matching "FAILED" or "ERROR" — should be >= 10 (new tests, some may error on missing imports)
      4. Verify no existing test changed from PASS to FAIL
    Expected Result: Existing tests PASS, new tests FAIL/ERROR
    Evidence: .sisyphus/evidence/task-1-red-tests.txt

  Scenario: Test structure is correct (not trivial assertions)
    Tool: Bash (grep)
    Preconditions: Tests written
    Steps:
      1. Run `grep -c "assert True" tests/cli/test_input_handler.py` — should be 0
      2. Run `grep -c "SWITCH_TO_SHELL\|SWITCH_TO_CHAT" tests/cli/test_input_handler.py` — should be >= 8
      3. Run `grep -c "def test_" tests/cli/test_input_handler.py` — should be >= 21 (8 existing + 13 new)
    Expected Result: No trivial assertions, sentinel constants used, 21+ test methods
    Evidence: .sisyphus/evidence/task-1-test-structure.txt
  ```

  **Commit**: YES
  - Message: `test: add failing tests for keystroke-level bash mode toggle`
  - Files: `tests/cli/test_input_handler.py`
  - Pre-commit: `uv run pytest tests/cli/test_input_handler.py -v` (expect new FAIL, old PASS)

- [x] 2. [GREEN] Implement InputHandler Keystroke Bindings + Sentinel Loop

  **What to do**:
  - Add sentinel constants at module level in `input_handler.py`:
    ```python
    SWITCH_TO_SHELL = "__SWITCH_TO_SHELL__"
    SWITCH_TO_CHAT = "__SWITCH_TO_CHAT__"
    ```
  - Add `!` key binding in `_setup_bindings()`:
    - Filter: `Condition(lambda: not self._shell_mode)` — only fires in chat mode
    - Inside handler: check `event.app.current_buffer.text == ""` and cursor at position 0
    - If condition met: `event.app.exit(result=SWITCH_TO_SHELL)` — consumes `!`, exits prompt
    - If condition NOT met: `event.app.current_buffer.insert_text("!")` — normal insert
  - Add Escape key binding:
    - `@self.bindings.add("escape", eager=True, filter=Condition(lambda: self._shell_mode))`
    - Inside handler: if buffer empty → `event.app.exit(result=SWITCH_TO_CHAT)`
    - If buffer non-empty → do nothing (let default Escape behavior handle it)
  - Add Backspace key binding:
    - `@self.bindings.add("backspace", filter=Condition(lambda: self._shell_mode))`
    - Inside handler: if buffer empty → `event.app.exit(result=SWITCH_TO_CHAT)`
    - If buffer non-empty → `event.app.current_buffer.delete_before_cursor(1)` (default behavior)
  - Add `shell_mode` read-only property and `exit_shell_mode()` public method:
    ```python
    @property
    def shell_mode(self) -> bool:
        return self._shell_mode

    def exit_shell_mode(self) -> None:
        """Reset to chat mode. Called by REPL when user types 'exit'/'quit' in shell."""
        self._shell_mode = False
    ```
  - Refactor `get_input()` to internal sentinel loop:
    ```python
    async def get_input(self, prompt_builder=None, *, shell_mode=False) -> str | None:
        self._shell_mode = shell_mode
        while True:
            session = self.shell_session if self._shell_mode else self.chat_session
            prompt = prompt_builder(self._shell_mode) if prompt_builder else "> "
            try:
                result = await session.prompt_async(prompt)
            except (EOFError, KeyboardInterrupt):
                return None
            if result is None:
                return None
            if result == SWITCH_TO_SHELL:
                self._shell_mode = True
                continue
            if result == SWITCH_TO_CHAT:
                self._shell_mode = False
                continue
            return result.strip()
    ```
  - NOTE: `get_input()` signature changes slightly — now accepts optional `prompt_builder` callable instead of static prompt. This allows re-building prompt on mode switch. The old `prompt` parameter can be kept for backward compat, or adapted. Follow the test expectations from Task 1.
  - Import `Condition` from `prompt_toolkit.filters`

  **Must NOT do**:
  - DO NOT touch repl.py (that's Task 3)
  - DO NOT change Ctrl-C, Ctrl-D, or Enter bindings
  - DO NOT modify build_prompt() styling
  - DO NOT add logging or debug prints
  - DO NOT create new files

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: prompt_toolkit key binding semantics require careful understanding of event model, Condition filters, and eager flag behavior
  - **Skills**: [`test-driven-development`]
    - `test-driven-development`: GREEN phase — implement until tests pass

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 1 (sequential after Task 1)
  - **Blocks**: Task 3
  - **Blocked By**: Task 1

  **References** (CRITICAL):

  **Pattern References**:
  - `src/coding_agent/cli/input_handler.py:86-116` — Existing `_setup_bindings()`: follow same decorator pattern for new bindings
  - `src/coding_agent/cli/input_handler.py:144-158` — Current `get_input()`: this is being refactored to add sentinel loop
  - `src/coding_agent/cli/input_handler.py:48-74` — `__init__`: shows dual session setup, shared bindings

  **API/Type References**:
  - `src/coding_agent/cli/input_handler.py:17` — `from prompt_toolkit.formatted_text import AnyFormattedText, FormattedText` — existing import pattern
  - `src/coding_agent/cli/input_handler.py:20` — `from prompt_toolkit.key_binding import KeyBindings` — already imported

  **External References**:
  - prompt_toolkit Condition filter: `from prompt_toolkit.filters import Condition` — creates dynamic filter for key bindings
  - prompt_toolkit `eager=True`: Required for Escape to fire immediately without waiting for Alt-key combo timeout (~500ms)
  - prompt_toolkit `event.app.exit(result=X)`: Sets prompt_async() return value to X

  **WHY Each Reference Matters**:
  - `input_handler.py:86-116`: New bindings must follow EXACTLY this pattern — `@self.bindings.add(key)` inside `_setup_bindings()`
  - `input_handler.py:144-158`: This is the function being refactored — understand current flow before adding sentinel loop
  - `eager=True` on Escape: Without this, Escape has ~500ms delay. This is a CRITICAL UX requirement discovered in Metis review

  **Acceptance Criteria**:

  **TDD (tests from Task 1):**
  - [ ] All 13 new tests from Task 1 now PASS
  - [ ] All 8 existing tests still PASS
  - [ ] `uv run pytest tests/cli/test_input_handler.py -v` → 21+ tests, 0 failures

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: All input handler tests pass (GREEN phase)
    Tool: Bash
    Preconditions: Task 1 tests exist and were previously failing
    Steps:
      1. Run `uv run pytest tests/cli/test_input_handler.py -v 2>&1`
      2. Count PASSED — should be >= 21
      3. Count FAILED — should be 0
    Expected Result: 21+ PASSED, 0 FAILED
    Evidence: .sisyphus/evidence/task-2-green-tests.txt

  Scenario: LSP diagnostics clean on input_handler.py
    Tool: lsp_diagnostics
    Preconditions: Implementation complete
    Steps:
      1. Run `lsp_diagnostics` on `src/coding_agent/cli/input_handler.py`
      2. Filter for errors
    Expected Result: 0 errors
    Evidence: .sisyphus/evidence/task-2-lsp-clean.txt

  Scenario: Sentinel constants are properly defined
    Tool: Bash (grep)
    Preconditions: Implementation complete
    Steps:
      1. Run `grep "SWITCH_TO_SHELL\|SWITCH_TO_CHAT" src/coding_agent/cli/input_handler.py`
      2. Verify both constants defined at module level
      3. Verify they're used in `_setup_bindings` and `get_input`
    Expected Result: Both constants defined and used consistently
    Evidence: .sisyphus/evidence/task-2-sentinels.txt

  Scenario: Escape binding uses eager=True
    Tool: Bash (grep)
    Preconditions: Implementation complete
    Steps:
      1. Run `grep -A2 "escape" src/coding_agent/cli/input_handler.py`
      2. Verify `eager=True` is present on escape binding
    Expected Result: `eager=True` found on escape key binding
    Evidence: .sisyphus/evidence/task-2-eager-escape.txt
  ```

  **Commit**: YES
  - Message: `feat: implement keystroke-level ! to shell mode switch`
  - Files: `src/coding_agent/cli/input_handler.py`
  - Pre-commit: `uv run pytest tests/cli/test_input_handler.py -v` (all PASS)

- [x] 3. Refactor REPL to Use InputHandler-Managed Shell Mode

  **What to do**:
  - **Remove `_shell_mode` attribute from `InteractiveSession`** (line 45 in repl.py) — mode is now managed by InputHandler internally
  - **Update `InteractiveSession.run()`**:
    - Remove `shell_mode=self._shell_mode` from `get_input()` call
    - Change `get_input()` invocation to pass a `prompt_builder` callable that reads `handler.shell_mode` to build the correct prompt dynamically on each loop iteration
    - After `get_input()` returns, check `self.input_handler.shell_mode` to decide execution path
    - Handle "exit"/"quit" in shell mode: call `self.input_handler.exit_shell_mode()` to reset (public API from Task 2)
  - **Remove `is_bash_mode_toggle()` call** (lines 122-128): Replaced by keystroke interception
  - **Remove `is_bash_mode_toggle` import** from repl.py
  - **Update shell mode check**: Replace `if self._shell_mode:` with `if self.input_handler.shell_mode:`
  - **Update REPL integration tests** in `tests/cli/test_repl.py`:
    - `test_bare_bang_enters_shell_mode_until_exit` (line 264): Update mock for new `get_input()` interface
    - `test_bang_bash_enters_shell_mode_until_exit` (line 304): Update or remove
    - `test_repl_passes_shell_mode_to_input_handler` (line 344): Rewrite to verify `handler.shell_mode` property
    - `test_repl_only_patches_stdout_while_waiting_for_input` (line 374): Minor update for new signature

  **Must NOT do**:
  - DO NOT touch input_handler.py (already done in Task 2)
  - DO NOT change BashExecutor or its execute() method
  - DO NOT modify _process_message() or agent pipeline
  - DO NOT change patch_stdout scope or welcome message styling

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: REPL refactoring requires understanding new InputHandler interface contract and updating 6+ integration tests
  - **Skills**: [`test-driven-development`]
    - `test-driven-development`: Verify refactored code passes all tests

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2 (sequential after Task 2)
  - **Blocks**: Task 4
  - **Blocked By**: Task 2

  **References** (CRITICAL):

  **Pattern References**:
  - `src/coding_agent/cli/repl.py:92-154` — `InteractiveSession.run()`: PRIMARY refactoring target
  - `src/coding_agent/cli/repl.py:105-114` — Current input block with `patch_stdout()` + `get_input()` — changes to prompt_builder pattern
  - `src/coding_agent/cli/repl.py:122-128` — `is_bash_mode_toggle` path: REMOVE entirely
  - `src/coding_agent/cli/repl.py:130-137` — Shell mode execution: keep logic, change condition source

  **Test References**:
  - `tests/cli/test_repl.py:248-301` — `test_bare_bang_enters_shell_mode_until_exit`: Uses old interface, must update
  - `tests/cli/test_repl.py:303-341` — `test_bang_bash_enters_shell_mode_until_exit`: Same update or remove
  - `tests/cli/test_repl.py:343-371` — `test_repl_passes_shell_mode_to_input_handler`: Complete rewrite needed
  - `tests/cli/test_repl.py:373-416` — `test_repl_only_patches_stdout_while_waiting_for_input`: Minor update

  **WHY Each Reference Matters**:
  - `repl.py:92-154`: THE method being refactored — every line matters
  - `repl.py:122-128`: Dead code REMOVAL — verify no other callers first
  - `test_repl.py:248-371`: These 4 tests WILL BREAK — each needs targeted update

  **Acceptance Criteria**:
  - [ ] `self._shell_mode` attribute removed from `InteractiveSession`
  - [ ] `is_bash_mode_toggle` import and call removed from repl.py
  - [ ] REPL uses `self.input_handler.shell_mode` for shell mode detection
  - [ ] All REPL bash integration tests updated and passing
  - [ ] `uv run pytest tests/cli/ -v` — ALL tests pass (55+ including new ones)

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: All CLI tests pass after REPL refactor
    Tool: Bash
    Steps:
      1. Run `uv run pytest tests/cli/ -v 2>&1`
      2. Count PASSED >= 55, FAILED == 0
    Expected Result: 55+ PASSED, 0 FAILED
    Evidence: .sisyphus/evidence/task-3-cli-tests.txt

  Scenario: UI tests not regressed
    Tool: Bash
    Steps:
      1. Run `uv run pytest tests/ui/ -v 2>&1`
      2. Count PASSED == 101, FAILED == 0
    Expected Result: 101 PASSED, 0 FAILED
    Evidence: .sisyphus/evidence/task-3-ui-regression.txt

  Scenario: _shell_mode removed from InteractiveSession
    Tool: Bash (grep)
    Steps:
      1. `grep "_shell_mode" src/coding_agent/cli/repl.py` — 0 matches
      2. `grep "is_bash_mode_toggle" src/coding_agent/cli/repl.py` — 0 matches
    Expected Result: No _shell_mode, no is_bash_mode_toggle in repl.py
    Evidence: .sisyphus/evidence/task-3-cleanup-verify.txt

  Scenario: LSP diagnostics clean on repl.py
    Tool: lsp_diagnostics
    Steps: Run lsp_diagnostics on repl.py, filter errors
    Expected Result: 0 errors
    Evidence: .sisyphus/evidence/task-3-lsp-repl.txt
  ```

  **Commit**: YES
  - Message: `refactor: update REPL to use InputHandler-managed shell mode`
  - Files: `src/coding_agent/cli/repl.py`, `tests/cli/test_repl.py`
  - Pre-commit: `uv run pytest tests/cli/ -v` (all PASS)

- [x] 4. Clean Up Dead Code + Update Help Text

  **What to do**:
  - **Remove `is_bash_mode_toggle()` function** from `bash_executor.py` (lines 17-19)
    - First: Use `lsp_find_references` or `ast_grep_search` to confirm no remaining callers
  - **Evaluate `is_bash_command()` / `extract_bash_command()`**:
    - With keystroke `!` interception, `!ls` in chat → `!` switches to shell → `ls` typed in shell. So `is_bash_command()` route in REPL is dead code
    - Remove `is_bash_command` and `extract_bash_command` imports from repl.py
    - Keep functions in `bash_executor.py` as utilities
  - **Update help/welcome text** in `repl.py`:
    - Old: `"Type /help for commands, ! for bash mode, !<cmd> for one-off shell, or just chat.\n"`
    - New: `"Type /help for commands, ! to enter bash mode, or just chat.\n"`
  - **Update `/help` command** in `commands.py` if it mentions `!` or bash mode
  - **Remove `is_bash_mode_toggle` tests** from `test_bash_executor.py`
  - **Remove `is_bash_command` usage block** from repl.py (lines 143-147) if confirmed dead
  - **Clean up unused imports** in all modified files

  **Must NOT do**:
  - DO NOT touch BashExecutor.execute() or its tests
  - DO NOT change any behavior — purely cleanup
  - DO NOT remove is_bash_command/extract_bash_command from bash_executor.py (keep as utility)
  - DO NOT change prompt styling or key bindings

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Straightforward dead code removal and text updates
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2 (after Task 3)
  - **Blocks**: F1-F4
  - **Blocked By**: Task 3

  **References** (CRITICAL):

  **Pattern References**:
  - `src/coding_agent/cli/bash_executor.py:17-19` — `is_bash_mode_toggle()`: Function to remove
  - `src/coding_agent/cli/repl.py:19-24` — Import block: remove unused imports
  - `src/coding_agent/cli/repl.py:97-101` — Welcome text to update
  - `src/coding_agent/cli/repl.py:143-147` — `is_bash_command()` usage block: dead code
  - `src/coding_agent/cli/commands.py` — `/help` command output: update bash mode description if present

  **Test References**:
  - `tests/cli/test_bash_executor.py` — Tests for `is_bash_mode_toggle` to remove

  **WHY Each Reference Matters**:
  - `bash_executor.py:17-19`: Dead function after keystroke interception
  - `repl.py:19-24`: Stale imports cause LSP warnings
  - `repl.py:97-101`: Help text must match new UX

  **Acceptance Criteria**:
  - [ ] `is_bash_mode_toggle` function removed from bash_executor.py
  - [ ] No stale imports in repl.py
  - [ ] Welcome text updated to reflect instant `!` switch
  - [ ] `uv run pytest tests/ -v` — ALL tests pass
  - [ ] `basedpyright` clean on all modified files

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: All tests pass after cleanup
    Tool: Bash
    Steps:
      1. Run `uv run pytest tests/ -v 2>&1`
      2. FAILED == 0
    Expected Result: All tests pass
    Evidence: .sisyphus/evidence/task-4-all-tests.txt

  Scenario: No stale references to removed code
    Tool: Bash (grep)
    Steps:
      1. `grep -r "is_bash_mode_toggle" src/coding_agent/` — 0 matches (or only bash_executor.py if kept)
      2. `grep -r "is_bash_mode_toggle" tests/` — 0 stale test references
    Expected Result: No stale references in calling code
    Evidence: .sisyphus/evidence/task-4-stale-refs.txt

  Scenario: Help text reflects new UX
    Tool: Bash (grep)
    Steps:
      1. `grep "!<cmd>" src/coding_agent/cli/repl.py` — 0 matches
      2. `grep "! to enter bash" src/coding_agent/cli/repl.py` — 1 match
    Expected Result: Old text removed, new text present
    Evidence: .sisyphus/evidence/task-4-help-text.txt
  ```

  **Commit**: YES
  - Message: `chore: remove dead bash toggle code and update help text`
  - Files: `src/coding_agent/cli/bash_executor.py`, `src/coding_agent/cli/repl.py`, `src/coding_agent/cli/commands.py`, `tests/cli/test_bash_executor.py`
  - Pre-commit: `uv run pytest tests/ -v` (all PASS)

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.

- [x] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, run command). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

  **QA Scenarios:**
  ```
  Scenario: All Must Have requirements implemented
    Tool: Bash + Read
    Steps:
      1. Read `src/coding_agent/cli/input_handler.py` — verify SWITCH_TO_SHELL/SWITCH_TO_CHAT constants exist
      2. Grep for `eager=True` in input_handler.py — must be present on Escape binding
      3. Grep for `Condition` import in input_handler.py — must be present
      4. Read `src/coding_agent/cli/repl.py` — verify no `_shell_mode` attribute on InteractiveSession
      5. Run `uv run pytest tests/cli/ -v 2>&1` — all pass
      6. Run `uv run pytest tests/ui/ -v 2>&1` — 101 pass
      7. Check `.sisyphus/evidence/` for task-1 through task-4 evidence files
    Expected Result: All Must Have items verified, all tests pass
    Evidence: .sisyphus/evidence/f1-compliance.txt

  Scenario: All Must NOT Have guardrails respected
    Tool: Bash (grep)
    Steps:
      1. `grep -r "logging\|logger\|print(" src/coding_agent/cli/input_handler.py` — no debug prints (print_pt is allowed)
      2. `git diff --name-only` — no new source/test files created (only existing files modified)
      3. `grep "BashExecutor" src/coding_agent/cli/input_handler.py` — 0 matches (not touched)
      4. Verify Ctrl-C and Ctrl-D bindings unchanged in input_handler.py
    Expected Result: Zero guardrail violations
    Evidence: .sisyphus/evidence/f1-guardrails.txt
  ```

- [x] F2. **Code Quality Review** — `unspecified-high`
  Run `basedpyright` on `src/coding_agent/cli/input_handler.py` and `src/coding_agent/cli/repl.py`. Review all changed files for: `as any`/`# type: ignore`, empty catches, print() in prod (should be print_pt), commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names.
  Output: `LSP [PASS/FAIL] | Tests [N pass/N fail] | Files [N clean/N issues] | VERDICT`

  **QA Scenarios:**
  ```
  Scenario: LSP diagnostics clean
    Tool: lsp_diagnostics
    Steps:
      1. Run lsp_diagnostics on `src/coding_agent/cli/input_handler.py` with severity=error
      2. Run lsp_diagnostics on `src/coding_agent/cli/repl.py` with severity=error
      3. Run lsp_diagnostics on `src/coding_agent/cli/bash_executor.py` with severity=error
    Expected Result: 0 errors across all files
    Evidence: .sisyphus/evidence/f2-lsp.txt

  Scenario: No code quality issues
    Tool: Bash (grep)
    Steps:
      1. `grep -n "# type: ignore\|as any\|noqa" src/coding_agent/cli/input_handler.py src/coding_agent/cli/repl.py` — 0 matches
      2. `grep -n "TODO\|FIXME\|HACK\|XXX" src/coding_agent/cli/input_handler.py src/coding_agent/cli/repl.py` — 0 new items
      3. `grep -c "^#\|^    #" src/coding_agent/cli/input_handler.py` — comment ratio reasonable (not excessive)
    Expected Result: Clean code, no suppressed warnings, no hacks
    Evidence: .sisyphus/evidence/f2-quality.txt
  ```

- [x] F3. **Real Manual QA** — `unspecified-high`
  Run `uv run pytest tests/cli/ -v` and `uv run pytest tests/ui/ -v`. Verify ALL tests pass. Check test coverage: each new binding has a test, each sentinel path has a test, each edge case has a test. Verify no test uses `assert True` or trivial assertions.
  Output: `CLI Tests [N/N pass] | UI Tests [N/N pass] | Coverage [adequate/gaps] | VERDICT`

  **QA Scenarios:**
  ```
  Scenario: Full test suite passes
    Tool: Bash
    Steps:
      1. Run `uv run pytest tests/cli/ -v 2>&1` — count PASSED and FAILED
      2. Run `uv run pytest tests/ui/ -v 2>&1` — count PASSED and FAILED
      3. Total CLI PASSED >= 60, FAILED == 0
      4. Total UI PASSED == 101, FAILED == 0
    Expected Result: All tests pass, no regressions
    Evidence: .sisyphus/evidence/f3-tests.txt

  Scenario: Test quality verification
    Tool: Bash (grep)
    Steps:
      1. `grep -c "assert True" tests/cli/test_input_handler.py` — must be 0
      2. `grep -c "def test_" tests/cli/test_input_handler.py` — must be >= 21
      3. `grep -c "SWITCH_TO_SHELL\|SWITCH_TO_CHAT" tests/cli/test_input_handler.py` — must be >= 8
      4. Verify each new binding (!, Escape, Backspace) has at least 2 tests (happy + edge)
    Expected Result: No trivial tests, adequate coverage for all new features
    Evidence: .sisyphus/evidence/f3-quality.txt
  ```

- [x] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff (git log/diff). Verify: everything in spec was built, nothing beyond spec was built. Check "Must NOT do" compliance. Detect cross-task contamination. Flag unaccounted changes.
  Output: `Tasks [N/N compliant] | Scope [CLEAN/N issues] | VERDICT`

  **QA Scenarios:**
  ```
  Scenario: Scope fidelity per task
    Tool: Bash (git)
    Steps:
      1. Run `git log --oneline -4` to see 4 commits
      2. For each commit, run `git diff <commit>^ <commit> --stat` to see files changed
      3. Verify commit 1 only touches `tests/cli/test_input_handler.py`
      4. Verify commit 2 only touches `src/coding_agent/cli/input_handler.py`
      5. Verify commit 3 only touches `src/coding_agent/cli/repl.py` and `tests/cli/test_repl.py`
      6. Verify commit 4 only touches `bash_executor.py`, `repl.py`, `commands.py`, `test_bash_executor.py`
    Expected Result: Each commit touches only its designated files
    Evidence: .sisyphus/evidence/f4-scope.txt

  Scenario: No cross-task contamination
    Tool: Bash (git diff)
    Steps:
      1. Run `git diff HEAD~4..HEAD -- src/coding_agent/cli/input_handler.py` — verify only Task 2 changes
      2. Run `git diff HEAD~4..HEAD -- src/coding_agent/ui/` — should be empty (UI untouched)
      3. Run `git diff HEAD~4..HEAD -- src/coding_agent/core/` — should be empty (core untouched)
    Expected Result: Changes confined to designated files, no contamination
    Evidence: .sisyphus/evidence/f4-contamination.txt
  ```

---

## Commit Strategy

| Commit | Message | Files | Pre-commit |
|--------|---------|-------|------------|
| 1 | `test: add failing tests for keystroke-level bash mode toggle` | `tests/cli/test_input_handler.py` | `uv run pytest tests/cli/test_input_handler.py -v` (expect new tests FAIL) |
| 2 | `feat: implement keystroke-level ! to shell mode switch` | `src/coding_agent/cli/input_handler.py` | `uv run pytest tests/cli/test_input_handler.py -v` (expect PASS) |
| 3 | `refactor: update REPL to use InputHandler-managed shell mode` | `src/coding_agent/cli/repl.py`, `tests/cli/test_repl.py` | `uv run pytest tests/cli/ -v` (all PASS) |
| 4 | `chore: remove dead bash toggle code and update help text` | `src/coding_agent/cli/bash_executor.py`, `tests/cli/test_bash_executor.py`, `src/coding_agent/cli/repl.py`, `src/coding_agent/cli/commands.py` | `uv run pytest tests/ -v` (all PASS) |

---

## Success Criteria

### Verification Commands
```bash
uv run pytest tests/cli/ -v    # Expected: 60+ tests pass, 0 fail
uv run pytest tests/ui/ -v     # Expected: 101 tests pass, 0 fail
basedpyright src/coding_agent/cli/input_handler.py  # Expected: 0 errors
basedpyright src/coding_agent/cli/repl.py           # Expected: 0 errors
```

### Final Checklist
- [ ] `!` on empty buffer → instant shell mode switch (no Enter needed)
- [ ] `!` consumed, never appears in buffer
- [ ] Shell prompt visually distinct (`bash <dir> $`)
- [ ] Escape → instant exit to chat (no delay)
- [ ] Backspace on empty shell buffer → exit to chat
- [ ] Normal `!`/Escape/Backspace behavior preserved in other contexts
- [ ] Separate history for chat and shell
- [ ] All "Must NOT Have" guardrails respected
- [ ] Zero LSP errors
- [ ] All tests pass
