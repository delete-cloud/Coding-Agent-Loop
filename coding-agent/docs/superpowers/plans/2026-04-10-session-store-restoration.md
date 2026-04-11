# Session Store Restoration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore `src/coding_agent/ui/session_store.py` from history, add only the minimum current-main compatibility needed, and re-enable the real `python -m coding_agent` entry path.

**Architecture:** Recover the module from commit `0fa8747`, compare it against `ea03f0f` and current callers, and keep the repair local to `session_store.py`. The only allowed compatibility drift is a thin `InMemorySessionStore.get()` shim if the current main-line tests still require it.

**Tech Stack:** Python 3.12, Click CLI, FastAPI HTTP server, pytest, uv, git worktrees, optional Redis client loading via `importlib`.

---

## File Map

- Create: `src/coding_agent/ui/session_store.py` — restored UI session persistence module
- Verify against: `src/coding_agent/ui/session_manager.py` — current store contract consumer
- Verify against: `src/coding_agent/ui/http_server.py` — readiness and session-creation path through `session_manager._store`
- Verify against: `tests/ui/test_session_manager_public_api.py` — current public API and persistence behavior
- Verify against: `tests/ui/test_http_server.py` — current `/readyz` and session-creation behavior
- Historical reference only: commit `0fa87471d64052d36eab52e4899d65f0a2675831` version of `src/coding_agent/ui/session_store.py`
- Historical reference only: commit `ea03f0fd51f79e9b946f0da91847b3dc760f735a` version of `src/coding_agent/ui/session_store.py`

---

### Task 1: Create a clean repair worktree and reproduce the current failure

**Files:**
- Verify: `src/coding_agent/__main__.py:1-40`
- Verify: `src/coding_agent/ui/__init__.py:1-12`
- Verify: `src/coding_agent/ui/session_manager.py:164-280`

- [ ] **Step 1: Create a dedicated repair worktree from the current main-line HEAD**

Run:

```bash
GIT_MASTER=1 git worktree add ../.worktrees/session-store-restoration wip/session-store-restoration
```

Expected: a new clean worktree exists at `../.worktrees/session-store-restoration` on branch `wip/session-store-restoration`.

- [ ] **Step 2: Verify the reproduced failure before touching code**

Run inside the new worktree:

```bash
uv run python -m coding_agent --help
```

Expected: FAIL with `ModuleNotFoundError: No module named 'coding_agent.ui.session_store'`.

- [ ] **Step 3: Verify the current tests that prove the missing module matters**

Run inside the new worktree:

```bash
uv run pytest tests/ui/test_session_manager_public_api.py -v
uv run pytest tests/ui/test_http_server.py -v
```

Expected: at least one of these runs fails during import/collection or runtime because `coding_agent.ui.session_store` is missing.

---

### Task 2: Restore the historical session store module from `0fa8747`

**Files:**
- Create: `src/coding_agent/ui/session_store.py`
- Historical reference: `0fa87471d64052d36eab52e4899d65f0a2675831:./src/coding_agent/ui/session_store.py`

- [ ] **Step 1: Restore the exact historical module from the chosen baseline**

Run inside the repair worktree:

```bash
GIT_MASTER=1 git show 0fa87471d64052d36eab52e4899d65f0a2675831:./src/coding_agent/ui/session_store.py > src/coding_agent/ui/session_store.py
```

Expected: `src/coding_agent/ui/session_store.py` now exists and matches the `0fa8747` version.

- [ ] **Step 2: Confirm the restored file still exposes the required public surface**

The restored file must contain these definitions:

```python
class SessionStore(Protocol):
    def save(self, session_id: str, data: SessionPayload) -> None: ...
    def load(self, session_id: str) -> SessionPayload | None: ...
    def list_sessions(self) -> list[str]: ...
    def delete(self, session_id: str) -> None: ...
    def check_health(self) -> bool: ...


class InMemorySessionStore:
    ...


class RedisSessionStore:
    ...


def create_session_store(... ) -> SessionStore:
    ...
```

Expected: all four public exports exist before any compatibility edits are considered.

---

### Task 3: Apply the only allowed compatibility shim if current-main still needs it

**Files:**
- Modify: `src/coding_agent/ui/session_store.py`
- Verify against: `tests/ui/test_session_manager_public_api.py:55-67`

- [ ] **Step 1: Compare the restored file against the current main-line caller expectations**

Check whether the current test suite still requires `InMemorySessionStore.get()`.

Current-main compatibility shim, if needed:

```python
class InMemorySessionStore:
    ...

    def get(self, session_id: str) -> SessionPayload | None:
        return self.load(session_id)
```

Expected: only add this method if the current tests still use `store.get(session_id)`.

- [ ] **Step 2: Do not add any broader compatibility layer**

Forbidden changes during this task:

```python
# DO NOT convert the module to async methods
async def save(...): ...

# DO NOT wrap unrelated agentkit storage types
from agentkit.storage.session import FileSessionStore

# DO NOT rewrite SessionManager to fit a different abstraction
```

Expected: the diff remains local to `src/coding_agent/ui/session_store.py` unless a read-only verification step proves otherwise.

---

### Task 4: Verify the repaired module through the real entry path and focused tests

**Files:**
- Verify: `src/coding_agent/ui/session_store.py`
- Test: `tests/ui/test_session_manager_public_api.py`
- Test: `tests/ui/test_http_server.py`

- [ ] **Step 1: Verify the direct module import succeeds**

Run inside the repair worktree:

```bash
uv run python -c "from coding_agent.ui.session_store import SessionStore, InMemorySessionStore, RedisSessionStore, create_session_store; print('OK')"
```

Expected: prints `OK`.

- [ ] **Step 2: Verify the real CLI entry path no longer breaks**

Run inside the repair worktree:

```bash
uv run python -m coding_agent --help
```

Expected: exits `0` and prints Click help text instead of `ModuleNotFoundError`.

- [ ] **Step 3: Run the public API session-store test file**

Run inside the repair worktree:

```bash
uv run pytest tests/ui/test_session_manager_public_api.py -v
```

Expected: PASS.

- [ ] **Step 4: Run the HTTP server session-store test file**

Run inside the repair worktree:

```bash
uv run pytest tests/ui/test_http_server.py -v
```

Expected: PASS.

- [ ] **Step 5: Run LSP diagnostics on the restored module and its direct consumer**

Check:

```text
src/coding_agent/ui/session_store.py
src/coding_agent/ui/session_manager.py
```

Expected: zero LSP errors in both files.

---

### Task 5: Final review and handoff back to the blocked integration flow

**Files:**
- Verify: `docs/superpowers/specs/2026-04-10-session-store-restoration-design.md`
- Verify: `docs/superpowers/plans/2026-04-10-session-store-restoration.md`

- [ ] **Step 1: Confirm the repair stayed within spec bounds**

Checklist:

```text
- session_store restored from history
- only minimal compatibility added
- no agentkit.storage refactor
- no async conversion
- no anchor+tape Task 13+ work mixed into this branch
```

Expected: every box is true.

- [ ] **Step 2: Capture the grounded handoff decision**

Record this implementation outcome in the final summary:

```text
session_store restoration verified; anchor+tape integration may resume at Task 13 gate
```

Expected: the repair branch ends with a clear conclusion and does not blur into the anchor+tape merge work.

---

## Self-Review

- Spec coverage check: the plan covers historical restoration, narrow compatibility, real CLI verification, direct import verification, and both required test files.
- Placeholder scan: no `TBD`, `TODO`, or vague “run related tests” language remains.
- Type consistency check: the only compatibility shim explicitly allowed is `InMemorySessionStore.get() -> self.load()`; no async/store-architecture drift is planned.
