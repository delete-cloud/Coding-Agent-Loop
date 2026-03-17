# run_one Worktree Integration Tests — Design Spec

## Goal

Add integration tests for `run_one(..., isolate_worktree=True)` that exercise real git worktree creation, overlay materialization, and cleanup — while mocking only the agent-loop subprocess call.

## Background

Existing `run_one` tests in `eval/tests/test_run_ab.py` mock `subprocess.run` globally with `isolate_worktree=False`. This means `prepare_isolated_repo()`, `materialize_overlay()`, and `cleanup_isolated_repo()` are never exercised. The gap: no test verifies the real worktree lifecycle.

## Architecture

- **New file**: `eval/tests/test_run_one_integration.py`
- **Test class**: `RunOneWorktreeIntegrationTests`
- **Mock strategy**: Selective proxy — only intercept calls where `cmd[0] == agent_loop_bin`, delegate all other commands (especially `git worktree`) to real `subprocess.run`
- **Repo strategy**: Each test creates an independent temporary git repo with an initial commit, then tears it down

## Helpers

### 1. `selective_subprocess_mock`

Context manager that patches `eval.ab.run_ab.subprocess.run`.

```python
@contextmanager
def selective_subprocess_mock(
    agent_loop_bin: str,
    *,
    result: subprocess.CompletedProcess | None = None,
    side_effect: Exception | None = None,
    on_agent_call: Callable | None = None,
) -> Generator[list, None, None]:
```

- When `cmd[0] == agent_loop_bin`: runs `on_agent_call(cmd, **kwargs)` if provided, then returns `result` or raises `side_effect`
- When `cmd[0] != agent_loop_bin`: delegates to real `subprocess.run`
- Yields `calls: list` recording intercepted agent-loop invocations

### 2. `create_test_git_repo`

Creates a minimal git repo suitable for `git worktree add --detach ... HEAD`.

```python
def create_test_git_repo() -> tuple[str, Callable[[], None]]:
```

- Creates tempdir
- `git init` → `git config user.email/name` → writes `README.md` → `git add . && git commit -m init`
- Does NOT include overlay files in the commit (see overlay test design below)
- Returns `(repo_path, cleanup_fn)`

## Test Cases

### Test 1: `test_isolate_worktree_creates_and_cleans_up`

**Setup**: Real git repo. Agent-loop mock returns `completed`.

**Assertions**:
- `run_one` returns `status == "completed"`
- The `--repo` path passed to agent-loop existed during the call (verified via `on_agent_call`)
- After `run_one` returns, the `--repo` path no longer exists

### Test 2: `test_isolate_worktree_materializes_overlay`

**Setup**: Real git repo. After initial commit, create `eval/ab/kb/test_doc.md` in base repo working tree (NOT committed). This file will not exist in the worktree initially (worktree is detached at HEAD). Only `materialize_overlay` can copy it.

**Assertions**:
- In `on_agent_call` hook: assert `eval/ab/kb/test_doc.md` exists in the worktree `--repo` path
- Assert file content matches what was written in the base repo

### Test 3: `test_isolate_worktree_cleans_up_on_timeout`

**Setup**: Real git repo. Agent-loop mock raises `subprocess.TimeoutExpired`.

**Assertions**:
- `run_one` returns `timed_out == True`
- `run_one` returns `status == "failed"`
- The `--repo` path no longer exists after return

### Test 4: `test_isolate_worktree_fails_gracefully_on_prepare_error`

**Setup**: No real git repo needed. Mock `prepare_isolated_repo` to raise `RuntimeError`.

**Assertions**:
- `run_one` returns `status == "failed"`
- `summary` contains `"isolate worktree failed"`
- No crash

### Test 5: `test_rate_limiter_released_on_timeout`

**Setup**: `isolate_worktree=False`. Agent-loop mock raises `TimeoutExpired`. Pass `_RateLimiter(max_concurrent=1)`.

**Assertions**:
- After `run_one` returns, `limiter._sem.acquire(blocking=False)` succeeds (semaphore was released)
- Manual `release()` after the check to restore state

## Non-Goals

- No DB fixture (`create_test_state_db`) — none of these 5 tests exercise DB recovery
- No changes to existing files
- No changes to production code (`run_ab.py`)

## Run Commands

```bash
# Integration tests only
python3 -m unittest eval.tests.test_run_one_integration -v

# Existing unit tests (must still pass)
python3 -m unittest eval.tests.test_run_ab -v
cd eval/ab && python3 -m unittest test_run_ab_strict -v
```
