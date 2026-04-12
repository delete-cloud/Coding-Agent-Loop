# Global CLI Options Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `--provider`, `--model`, `--base-url`, and `--api-key` truly global CLI options shared by the default REPL path and the `run` / `repl` subcommands, while keeping `run`-only and `repl`-only flags local to those commands.

**Architecture:** Move shared runtime option parsing to the root Click group, store resolved root option values in `ctx.obj`, and let `run` / `repl` merge those shared values with their command-specific options before building config. Centralize provider choice definitions so root and subcommands cannot drift, and route command config creation through a shared helper that preserves provider-specific envvar fallback behavior from `load_config`.

**Tech Stack:** Python, Click, Pydantic Config model, pytest, unittest.mock

---

### Task 1: Add failing tests for shared root option propagation

**Files:**
- Modify: `coding-agent/tests/coding_agent/test_cli_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Add tests that assert:

```python
def test_run_uses_root_provider_and_model_options():
    ...

def test_repl_uses_root_provider_and_model_options():
    ...

def test_run_uses_kimi_code_api_key_env_without_cli_api_key():
    ...
```

The tests should patch the execution boundary (`_run_headless` / `run_repl`) and assert the resulting config values, not just Click parsing.

- [ ] **Step 2: Run test subset to verify it fails**

Run: `uv run pytest coding-agent/tests/coding_agent/test_cli_pipeline.py -k "root_provider or kimi_code_api_key" -q`

Expected: FAIL because root options are not propagated into `run` / `repl`, and `run` still requires `--api-key` instead of letting provider-specific env resolution happen.

- [ ] **Step 3: Commit test-only red state if needed locally**

No commit required here; proceed immediately to implementation once failure is confirmed.

### Task 2: Centralize shared CLI runtime options

**Files:**
- Modify: `coding-agent/src/coding_agent/__main__.py`

- [ ] **Step 1: Add a shared provider choice constant/helper**

Introduce one shared provider option source for CLI-exposed providers so root and subcommands reuse the same choice definition.

- [ ] **Step 2: Add a root option collector helper**

Add a helper that captures root-level `provider`, `model`, `base_url`, and `api_key` into `ctx.obj`.

- [ ] **Step 3: Add a shared config builder helper**

Create a helper that merges:
- root shared options from `ctx.obj`
- subcommand-specific option overrides
- envvar/provider fallback through `load_config`

This helper must be used by:
- default root REPL path
- `run`
- `repl`

- [ ] **Step 4: Remove duplicated shared runtime options from subcommands**

Keep only command-specific options on:
- `run`: `goal`, `repo`, `max_steps`, `approval`, `parallel`, `max_parallel`, `cache`, `cache_size`, `tui`
- `repl`: `repo`, `max_steps`

- [ ] **Step 5: Keep root non-TTY guard behavior intact**

Preserve the friendly error for `python -m coding_agent` when stdout is not a TTY.

### Task 3: Make tests pass with minimal implementation

**Files:**
- Modify: `coding-agent/src/coding_agent/__main__.py`
- Modify: `coding-agent/tests/coding_agent/test_cli_pipeline.py`

- [ ] **Step 1: Run the targeted tests after implementation**

Run: `uv run pytest coding-agent/tests/coding_agent/test_cli_pipeline.py -k "root_provider or kimi_code_api_key" -q`

Expected: PASS

- [ ] **Step 2: Expand assertions to cover command-specific flags still working**

Add/adjust one test showing `run`-only options like `--tui` or `--approval` remain command-local and are not moved to the root command.

- [ ] **Step 3: Run the full CLI pipeline test file**

Run: `uv run pytest coding-agent/tests/coding_agent/test_cli_pipeline.py -q`

Expected: PASS

### Task 4: Verify diagnostics and prepare branch for review

**Files:**
- Modify: `coding-agent/src/coding_agent/__main__.py`
- Modify: `coding-agent/tests/coding_agent/test_cli_pipeline.py`

- [ ] **Step 1: Run diagnostics on changed files**

Check `coding-agent/src/coding_agent/__main__.py`
Check `coding-agent/tests/coding_agent/test_cli_pipeline.py`

Expected: no new errors introduced by this change

- [ ] **Step 2: Review git diff for scope control**

Confirm only shared runtime option handling and direct regression tests changed.

- [ ] **Step 3: Commit**

```bash
GIT_MASTER=1 git add coding-agent/src/coding_agent/__main__.py coding-agent/tests/coding_agent/test_cli_pipeline.py docs/superpowers/plans/2026-04-12-global-cli-options.md
GIT_MASTER=1 git commit -m "fix(cli): unify shared root options" -m "Ultraworked with [Sisyphus](https://github.com/code-yeongyu/oh-my-openagent)" -m "Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>"
```
