# Session Store Restoration Design

## Goal

Restore `src/coding_agent/ui/session_store.py` so the current main working line can load the real `coding_agent` CLI entry path again.

This spec is intentionally narrow. It defines only the recovery of the missing UI session-store module and the verification needed to prove that the recovery is correct.

The target outcome is:

1. `uv run python -m coding_agent --help` no longer fails with `ModuleNotFoundError: coding_agent.ui.session_store`
2. the recovered module matches the historical design closely enough to be considered a restoration, not a redesign
3. the current callers and tests continue to work with only minimal compatibility adjustments

---

## Context

The current main working line still imports `coding_agent.ui.session_store` from:

- `src/coding_agent/ui/session_manager.py`
- `tests/ui/test_session_manager_public_api.py`

but the file itself is missing from the working tree.

Historical evidence shows that `session_store.py` previously existed and was introduced and refined very recently:

- `ea03f0f` — `feat(ui): persist session state and harden runtime approval cleanup`
- `0fa8747` — `fix(ui): remove type suppression and align session store APIs`

This means the current failure is not a greenfield design problem. It is a recovery problem:

- the public surface is already implied by current imports and tests
- the implementation shape is already implied by recent git history
- the safest path is to restore that design rather than invent a new storage abstraction

---

## Scope

### In scope

- restore `src/coding_agent/ui/session_store.py` from recent history
- use `0fa8747` as the primary recovery baseline
- allow only the smallest compatibility edits needed for the current callers and tests
- verify the real CLI import path, direct module imports, and focused UI/session tests

### Out of scope

- redesigning session persistence architecture
- moving `SessionStore` responsibilities into `agentkit.storage`
- unifying sync and async storage interfaces across the repo
- opportunistic refactors in `session_manager.py`, `http_server.py`, or unrelated UI code
- resuming anchor+tape integration tasks in the same change

---

## Design Principle

### Primary rule: restore first, adapt second

The historical implementation is the source of truth.

The repair should therefore follow this rule:

1. recover the module from the latest known good historical shape
2. compare that shape against the current working callers and tests
3. apply only the smallest compatibility edits needed to reconnect the restored module to the current main line

This is **not** permission to redesign the module. Compatibility changes are allowed only when they are needed to satisfy the current public contract already implied by the codebase.

### Compatibility ceiling

Allowed:

- restoring missing exports
- aligning a small method or helper detail with the current call sites
- keeping the current tests and startup path green without broadening behavior

Not allowed:

- introducing a new persistence model
- replacing the module with a wrapper around unrelated abstractions just because one exists elsewhere
- expanding behavior beyond what history and current callers already require

---

## Required Public Surface

The restored module must provide the public API currently expected by the main line:

- `SessionStore`
- `InMemorySessionStore`
- `RedisSessionStore`
- `create_session_store(...)`

From current usage, the effective contract is a synchronous session metadata store with:

- `save(session_id, data)`
- `load(session_id)`
- `list_sessions()`
- `delete(session_id)`
- `check_health()`

The design intent is not to create a broader generic storage layer. It is to restore the UI session persistence boundary that `SessionManager` already depends on.

For current-main compatibility, `InMemorySessionStore.get()` may be restored as a thin alias to `load()` if current tests still depend on it. This is a compatibility shim, not a new architectural commitment.

---

## Recovery Source Strategy

### Baseline source

Use `0fa8747` as the first recovery source because it already reflects a post-follow-up state where the session-store APIs were aligned and the earlier type-suppression workaround was removed.

### Historical cross-check

Use `ea03f0f` as the comparison source when deciding whether a current mismatch is:

- genuine historical intent, or
- a later accidental drift introduced by the missing-file situation

### Recovery preference

If `0fa8747` already satisfies current callers, keep it intact.

If a mismatch exists, prefer the smallest local compatibility change inside `session_store.py` rather than spreading edits across multiple callers.

---

## Execution Design

### 1. Restore the historical module

Recover `src/coding_agent/ui/session_store.py` from the `0fa8747` version and treat that as the initial candidate implementation.

### 2. Check current-caller compatibility

Validate the restored module against the current direct dependents:

- `src/coding_agent/ui/session_manager.py`
- `tests/ui/test_session_manager_public_api.py`
- `tests/ui/test_http_server.py`

This step must answer:

- whether the restored exports still match the imports
- whether `SessionManager` still uses the same store contract
- whether the store-backed health and persistence tests still describe the same behavior

### 3. Apply only minimal compatibility edits if required

If the restored historical file does not exactly match the current call surface, make the smallest change that restores compatibility without changing module purpose.

Examples of acceptable edits:

- restoring a helper method expected by a current test
- adjusting a narrow typing or import detail
- preserving current `create_session_store(...)` behavior if its contract is already validated by tests

Examples of unacceptable edits:

- changing the module to use async interfaces because another subsystem does
- collapsing Redis and in-memory behavior into a new abstraction layer
- changing `SessionManager` semantics just to fit a different storage design

### 4. Verify in three layers

#### Layer A — Real entry-path verification

The repair must prove that the real CLI path no longer breaks during import:

- `uv run python -m coding_agent --help`

This layer exists because a plain `import coding_agent` is weaker than the actual failing path.

#### Layer B — Direct module verification

Verify the restored public surface explicitly:

- import the restored classes and factory directly from `coding_agent.ui.session_store`

#### Layer C — Focused behavior verification

Run the focused UI/session tests that prove:

- store-backed session persistence behavior
- health-check behavior
- fallback behavior for unavailable Redis
- `SessionManager` integration with the restored store

The minimum required test files are:

- `tests/ui/test_session_manager_public_api.py`
- `tests/ui/test_http_server.py`

---

## Acceptance Criteria

This design is satisfied only if all of the following are true:

1. `src/coding_agent/ui/session_store.py` is restored from history and remains recognizably aligned with the historical implementation
2. the module exports the public surface expected by the current main line
3. `uv run python -m coding_agent --help` no longer fails on `ModuleNotFoundError: coding_agent.ui.session_store`
4. direct imports from `coding_agent.ui.session_store` succeed
5. `tests/ui/test_session_manager_public_api.py` and `tests/ui/test_http_server.py` pass
6. no unrelated architectural cleanup is bundled into the repair

---

## Risks and Non-Goals

### Main risk

The main risk is allowing a small recovery task to turn into an architecture rewrite because nearby abstractions appear similar.

That would blur the root cause, expand scope, and make it harder to tell whether the original missing-file failure was actually repaired.

### Secondary risk

The second risk is doing a purely mechanical restore that ignores small caller drift, producing a file that exists again but does not actually restore the working entry path.

This is why the design allows minimal compatibility work after restoration.

### Non-goal

This work is not intended to settle the long-term storage architecture of the project. It is only a targeted restoration of the UI session-store boundary.

---

## Recommended Next Step

Implement this recovery in a clean repair worktree using the following order:

1. restore `session_store.py` from `0fa8747`
2. compare it against `ea03f0f` and current callers
3. apply only minimal compatibility edits if needed
4. run entry-path verification
5. run direct-import verification
6. run `tests/ui/test_session_manager_public_api.py`
7. run `tests/ui/test_http_server.py`

Only after that repair is verified should the anchor+tape integration plan resume at the Task 13 gate.
