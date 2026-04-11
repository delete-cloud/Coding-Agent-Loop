# P4: HookSpec Runtime Enforcement

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make HookSpec a live runtime contract — validate return types at call time, eliminating the "type black hole" in the hook dispatch chain.

**Architecture:** Two-layer defense with clear role separation:
- **HookRuntime (primary):** Validates return types against `HookSpec.return_type` when specs are injected. Catches wrong types before Pipeline sees them.
- **Pipeline (defense-in-depth):** isinstance guards at 3 unsafe consumption sites. Covers the case where HookRuntime has no specs (tests, legacy code).

PluginRegistry gets optional `specs` param to warn on unknown hook names at registration time. `returns_directive` field is retained for backward compat but considered deprecated — `return_type=Directive` is the source of truth.

**Tech Stack:** Python 3.14, dataclasses, pytest

**⚠️ Review-driven amendments (from code review):**
1. `approve_tool_call` Pipeline guard must **fail closed** (reject), not auto-approve
2. `execute_tools_batch` must be added to HOOK_SPECS (Pipeline already calls it)
3. `ctx._handoff_done` is out of scope — do NOT mix handoff dedup into this task (P3 already added it)
4. `resolve_context_window` Pipeline guard must do structural validation (len==2, int check), not just isinstance(tuple)
5. Task 5 title corrected: "unknown hook warning" not "signature validation"
6. Tests must reference `create_agent()` not `build_pipeline_from_config()`

---

## TL;DR

> **Quick Summary**: Currently `HookRuntime.call_first/call_many` return whatever the plugin returns — no type checking. A plugin that accidentally returns `{"approved": True}` instead of `Approve()` for `approve_tool_call` silently passes through to Pipeline, where the `DirectiveExecutor` would hit `raise ValueError("unknown directive kind")` or worse, auto-approve unsafe tool calls. This plan closes that gap.
>
> **Deliverables**:
> - `HookTypeError` error class in error hierarchy
> - `HookSpec.return_type` field + 14-hook HOOK_SPECS (adds `execute_tools_batch`)
> - `HookRuntime(specs=HOOK_SPECS)` validates return types, raises `HookTypeError` on violation
> - `PluginRegistry(specs=HOOK_SPECS)` warns on unknown hook names at registration
> - Pipeline guards at 3 sites: `resolve_context_window` (structural), `approve_tool_call` (fail closed), `on_turn_end` (filter-before-store)
> - Both layers wired in `__main__.py`
>
> **Estimated Effort**: Medium (8 sequential tasks, ~15 new tests)
> **Parallel Execution**: NO — strict sequential chain (each task builds on the previous)
> **Critical Path**: T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8

---

## Context

### Original Request

Runtime type enforcement for the hook dispatch chain. The hook system defines `returns_directive` metadata but never checks it at dispatch time. The pipeline consumes hook results under the optimistic assumption they are correct types.

### Research Findings

- `HookRuntime.call_first/call_many` return raw plugin return values with zero isinstance checking
- `HOOK_SPECS` in `hookspecs.py` exists but only has `returns_directive: bool` — no `return_type` field to validate against
- Pipeline has 3 unsafe consumption sites:
  1. `_stage_build_context:150` — unpacks `window_result` as `(int, Entry)` tuple with no validation
  2. `_stage_run_model:270-272` — passes `directive` to `DirectiveExecutor` without checking it's a `Directive`
  3. `_stage_render:353-359` — iterates `on_turn_end` results without Directive check
- `execute_tools_batch` is called at `pipeline.py:293` but is absent from HOOK_SPECS — any plugin registering it would trigger no-op (currently harmless, but inconsistent)
- P3 already added `ctx._handoff_done` and `window_start` passthrough to `_stage_build_context` — Task 7 must preserve these
- `HookError` in `errors.py` has `hook_name` attribute — `HookTypeError` should extend it for consistency

### Backward Compat Constraints

- `HookRuntime(registry)` with no specs must work exactly as before — no validation, no errors
- `PluginRegistry()` with no specs must work exactly as before — no warnings
- `HookRuntime(registry, specs=HOOK_SPECS)` is the new opt-in path, wired in `__main__.py`

---

## Work Objectives

### Core Objective

Eliminate the type black hole in hook dispatch so that plugin return type violations surface immediately with clear error messages, rather than propagating silently to crash at an unrelated call site.

### Concrete Deliverables

- `errors.py`: `HookTypeError(HookError)` with `hook_name` attribute
- `hookspecs.py`: `HookSpec.return_type: type | None` field; 14-hook HOOK_SPECS including `execute_tools_batch`
- `hook_runtime.py`: `_validate_return()` called after every non-None result in `call_first`/`call_many`
- `registry.py`: `PluginRegistry(specs=)` warns on unknown hook name at `register()` time
- `pipeline.py`: structural guard on `resolve_context_window`, fail-closed guard on `approve_tool_call`, filter-before-store on `on_turn_end`
- `__main__.py`: both `HookRuntime` and `PluginRegistry` instantiated with `specs=HOOK_SPECS`

### Definition of Done

- [x] `uv run pytest tests/ -v` all pass, 0 failures
- [x] `uv run mypy src/agentkit/runtime/hook_runtime.py src/agentkit/runtime/hookspecs.py src/agentkit/plugin/registry.py src/agentkit/errors.py src/agentkit/runtime/pipeline.py` — no new errors
- [ ] Each task = one atomic commit with all tests green

### Must Have

- `HookRuntime` raises `HookTypeError` when hook returns wrong type (with specs wired)
- `HookRuntime` without specs works exactly as before (backward compat)
- `PluginRegistry` warns on unknown hook names (with specs wired)
- `PluginRegistry` without specs works exactly as before (backward compat)
- Pipeline `approve_tool_call` guard **rejects** (not approves) on invalid return type
- Pipeline drops/logs bad `on_turn_end` directives before storing in `ctx.output`
- Pipeline structurally validates `resolve_context_window` result: tuple, len==2, first element is int
- `execute_tools_batch` is in HOOK_SPECS with no `return_type` (return shape too dynamic)
- `None` is always allowed as hook return even when `return_type` is set (means "no opinion")

### Must NOT Have

- Do NOT break `HookRuntime(registry)` with no specs — must behave identically to current
- Do NOT introduce `ctx._handoff_done` (P3 already added it — preserve, do not duplicate)
- Do NOT modify `ApprovalPlugin` or `AskUser` handler logic
- Do NOT validate observer hooks (`on_error`, `on_checkpoint`) return types — they're fire-and-forget
- Do NOT validate `mount` return type — its return shape varies by plugin
- agentkit must NOT import coding_agent

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision

- **Infrastructure exists**: YES (pytest)
- **Automated tests**: TDD (RED → GREEN → REFACTOR)
- **Framework**: pytest (`uv run pytest`)
- **Each task**: Write failing test first, implement, verify pass, commit

### QA Policy

Every task includes agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/p4-task-{N}-{scenario-slug}.txt`.

---

## Execution Strategy

### Sequential Task Chain

```
Task 1: HookTypeError class         [quick]   — prereq for all
Task 2: HookSpec.return_type + 14 hooks [deep] — prereq for T3
Task 3: HookRuntime validation      [deep]    — prereq for T4, T7
Task 4: Wire specs → HookRuntime    [quick]   — prereq for T6
Task 5: PluginRegistry unknown warn [quick]   — prereq for T6
Task 6: Wire specs → PluginRegistry [quick]   — prereq for T8
Task 7: Pipeline isinstance guards  [deep]    — can start after T3
Task 8: End-to-end integration test [quick]   — after T6 + T7
```

### Dependency Matrix

| Task | Depends On | Blocks | Category |
|------|-----------|--------|----------|
| 1 | — | 2, 3 | `quick` |
| 2 | 1 | 3 | `deep` |
| 3 | 1, 2 | 4, 7 | `deep` |
| 4 | 3 | 6 | `quick` |
| 5 | — | 6 | `quick` |
| 6 | 4, 5 | 8 | `quick` |
| 7 | 3 | 8 | `deep` |
| 8 | 6, 7 | — | `quick` |

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `src/agentkit/errors.py` | Error types — add HookTypeError | Modify |
| `src/agentkit/runtime/hookspecs.py` | Hook metadata — add `return_type` field | Modify |
| `src/agentkit/runtime/hook_runtime.py` | Dispatch engine — add return type validation | Modify |
| `src/agentkit/plugin/registry.py` | Plugin registration — add unknown hook name warning | Modify |
| `src/agentkit/runtime/pipeline.py` | Pipeline — add isinstance guards at consumption sites | Modify |
| `src/coding_agent/__main__.py` | Wire specs into both HookRuntime and PluginRegistry | Modify |
| `tests/agentkit/runtime/test_hook_runtime.py` | HookRuntime tests — add validation tests | Modify |
| `tests/agentkit/runtime/test_hookspecs.py` | HookSpec tests — add return_type coverage | Modify |
| `tests/agentkit/plugin/test_registry.py` | Registry tests — add unknown hook warning tests | Modify or Create |
| `tests/agentkit/runtime/test_pipeline_typeguards.py` | Pipeline type guard tests | Create |
| `tests/coding_agent/test_cli_pipeline.py` | Wiring tests for __main__.py | Modify |

---

## TODOs

### Task 1: Add HookTypeError to error hierarchy

**Files:**
- Modify: `src/agentkit/errors.py:14-19`
- Test: `tests/agentkit/runtime/test_hook_runtime.py`

- [ ] **Step 1: Write the failing test**

```python
# In tests/agentkit/runtime/test_hook_runtime.py, add at top:
from agentkit.errors import HookTypeError

def test_hook_type_error_has_hook_name_and_detail():
    err = HookTypeError(
        "expected Directive, got dict",
        hook_name="approve_tool_call",
    )
    assert err.hook_name == "approve_tool_call"
    assert "expected Directive, got dict" in str(err)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agentkit/runtime/test_hook_runtime.py::test_hook_type_error_has_hook_name_and_detail -v`
Expected: FAIL with `ImportError: cannot import name 'HookTypeError'`

- [ ] **Step 3: Write minimal implementation**

In `src/agentkit/errors.py`, add after `HookError` (after line 19):

```python
class HookTypeError(HookError):
    """A hook returned a value that does not match its declared return type."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/agentkit/runtime/test_hook_runtime.py::test_hook_type_error_has_hook_name_and_detail -v`
Expected: PASS

- [ ] **Step 5: Run full suite to confirm no regressions**

Run: `uv run pytest tests/ -q --tb=short 2>&1 | tail -3`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/agentkit/errors.py tests/agentkit/runtime/test_hook_runtime.py
git commit -m "feat(errors): add HookTypeError for hook return type validation"
```

**QA Scenarios:**

```
Scenario: HookTypeError is importable and carries hook_name attribute
  Tool: Bash (uv run pytest)
  Steps:
    1. Run: uv run pytest tests/agentkit/runtime/test_hook_runtime.py::test_hook_type_error_has_hook_name_and_detail -v
    2. Assert: exit code 0, PASSED
  Expected Result: HookTypeError importable, hook_name attr accessible, message in str()
  Failure Indicators: ImportError, AttributeError

Scenario: HookTypeError inherits HookError
  Tool: Bash
  Steps:
    1. Run: uv run python -c "from agentkit.errors import HookTypeError, HookError; assert issubclass(HookTypeError, HookError); print('ok')"
    2. Assert: output is "ok"
  Expected Result: isinstance check passes for HookError
  Failure Indicators: AssertionError (wrong inheritance)
```

---

### Task 2: Add return_type to HookSpec + populate for all 14 hooks

**Files:**
- Modify: `src/agentkit/runtime/hookspecs.py:16-97`
- Modify: `tests/agentkit/runtime/test_hookspecs.py`

**⚠️ Note on existing test**: `test_all_13_hooks_defined` in `test_hookspecs.py` must be updated to `test_all_14_hooks_defined` and include `execute_tools_batch` in the expected set.

- [ ] **Step 1: Write the failing tests**

Add to `tests/agentkit/runtime/test_hookspecs.py`:

```python
from agentkit.directive.types import Directive
from agentkit.tape.models import Entry

class TestHookSpecReturnTypes:
    def test_approve_tool_call_declares_directive_return(self):
        spec = HOOK_SPECS["approve_tool_call"]
        assert spec.return_type is Directive

    def test_on_turn_end_declares_directive_return(self):
        spec = HOOK_SPECS["on_turn_end"]
        assert spec.return_type is Directive

    def test_resolve_context_window_declares_tuple_return(self):
        spec = HOOK_SPECS["resolve_context_window"]
        assert spec.return_type is tuple

    def test_provide_llm_has_no_return_type(self):
        spec = HOOK_SPECS["provide_llm"]
        assert spec.return_type is None

    def test_observer_hooks_have_no_return_type(self):
        for name in ("on_error", "on_checkpoint", "on_session_event"):
            spec = HOOK_SPECS[name]
            assert spec.return_type is None, f"{name} should not declare return_type"

    def test_every_hook_with_returns_directive_has_directive_return_type(self):
        for name, spec in HOOK_SPECS.items():
            if spec.returns_directive:
                assert spec.return_type is Directive, (
                    f"{name} has returns_directive=True but return_type is not Directive"
                )

    def test_execute_tools_batch_is_in_hook_specs(self):
        assert "execute_tools_batch" in HOOK_SPECS

    def test_execute_tools_batch_has_no_return_type(self):
        spec = HOOK_SPECS["execute_tools_batch"]
        assert spec.return_type is None
```

Also update the count test:
```python
# Replace test_all_13_hooks_defined with:
def test_all_14_hooks_defined(self):
    expected = {
        "provide_storage", "get_tools", "provide_llm", "approve_tool_call",
        "summarize_context", "resolve_context_window", "on_error", "mount",
        "on_checkpoint", "build_context", "on_turn_end", "execute_tool",
        "on_session_event", "execute_tools_batch",
    }
    assert set(HOOK_SPECS.keys()) == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agentkit/runtime/test_hookspecs.py::TestHookSpecReturnTypes -v`
Expected: FAIL with `AttributeError: ... has no attribute 'return_type'`

- [ ] **Step 3: Write minimal implementation**

Replace `src/agentkit/runtime/hookspecs.py` with the updated version. Key changes:
1. Add `from agentkit.directive.types import Directive` import
2. Add `return_type: type | None = None` field to `HookSpec` dataclass
3. Set `return_type=Directive` on `approve_tool_call` and `on_turn_end`
4. Set `return_type=tuple` on `resolve_context_window`
5. Set `return_type=list` on `build_context`
6. Add `execute_tools_batch` entry with no `return_type`
7. All other hooks: `return_type=None` (default, no validation)

Full replacement:

```python
"""Hook specifications — metadata for the 14 agentkit hooks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentkit.directive.types import Directive


@dataclass(frozen=True)
class HookSpec:
    """Metadata for a single hook."""

    name: str
    firstresult: bool = False
    is_observer: bool = False
    returns_directive: bool = False
    return_type: type | None = None
    doc: str = ""


HOOK_SPECS: dict[str, HookSpec] = {
    "provide_storage": HookSpec(
        name="provide_storage",
        firstresult=True,
        doc="Return a TapeStore instance (with optional ForkTapeStore wrapping).",
    ),
    "get_tools": HookSpec(
        name="get_tools",
        firstresult=False,
        doc="Collect tool schemas from all plugins. call_many gathers lists.",
    ),
    "provide_llm": HookSpec(
        name="provide_llm",
        firstresult=True,
        doc="Return an LLMProvider instance for the current session.",
    ),
    "approve_tool_call": HookSpec(
        name="approve_tool_call",
        firstresult=True,
        returns_directive=True,
        return_type=Directive,
        doc="Return Approve/Reject/AskUser directive for a tool call.",
    ),
    "summarize_context": HookSpec(
        name="summarize_context",
        firstresult=True,
        doc="Compress tape entries when context window is exhausted.",
    ),
    "resolve_context_window": HookSpec(
        name="resolve_context_window",
        firstresult=True,
        return_type=tuple,
        doc="Return (window_start_index: int, summary_anchor: Entry | None) or None.",
    ),
    "on_error": HookSpec(
        name="on_error",
        is_observer=True,
        doc="Observer: notified on pipeline errors. Cannot affect flow.",
    ),
    "mount": HookSpec(
        name="mount",
        firstresult=False,
        doc="Plugin initialization. Returns initial plugin state dict.",
    ),
    "on_checkpoint": HookSpec(
        name="on_checkpoint",
        is_observer=True,
        doc="Observer: notified at turn boundaries for state persistence.",
    ),
    "build_context": HookSpec(
        name="build_context",
        firstresult=False,
        return_type=list,
        doc="Inject grounding context (memories, KB results) before prompt build.",
    ),
    "on_turn_end": HookSpec(
        name="on_turn_end",
        firstresult=False,
        returns_directive=True,
        return_type=Directive,
        doc="finish_action: produce MemoryRecord directive at turn end.",
    ),
    "execute_tool": HookSpec(
        name="execute_tool",
        firstresult=True,
        doc="Execute a tool by name and return the result.",
    ),
    "on_session_event": HookSpec(
        name="on_session_event",
        is_observer=True,
        doc="Observer: notified on session-level events.",
    ),
    "execute_tools_batch": HookSpec(
        name="execute_tools_batch",
        firstresult=True,
        doc="Execute multiple tool calls in parallel. Return shape too dynamic to validate.",
    ),
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agentkit/runtime/test_hookspecs.py -v`
Expected: ALL PASS (both old and new tests)

- [ ] **Step 5: Run full suite**

Run: `uv run pytest tests/ -q --tb=short 2>&1 | tail -3`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/agentkit/runtime/hookspecs.py tests/agentkit/runtime/test_hookspecs.py
git commit -m "feat(hookspecs): add return_type field + execute_tools_batch to HookSpec"
```

**QA Scenarios:**

```
Scenario: return_type field accessible on all specs
  Tool: Bash
  Steps:
    1. Run: uv run python -c "from agentkit.runtime.hookspecs import HOOK_SPECS; from agentkit.directive.types import Directive; assert HOOK_SPECS['approve_tool_call'].return_type is Directive; assert HOOK_SPECS['provide_llm'].return_type is None; assert 'execute_tools_batch' in HOOK_SPECS; print('ok')"
    2. Assert: output is "ok"
  Expected Result: return_type populated correctly, execute_tools_batch present
  Failure Indicators: AttributeError, AssertionError

Scenario: existing hookspec tests still pass
  Tool: Bash (uv run pytest)
  Steps:
    1. Run: uv run pytest tests/agentkit/runtime/test_hookspecs.py -v
    2. Assert: exit code 0, all pass
  Expected Result: Both old structural tests and new return_type tests green
  Failure Indicators: Any failure in old tests (backward compat broken)
```

---

### Task 3: HookRuntime return type validation

**Files:**
- Modify: `src/agentkit/runtime/hook_runtime.py`
- Modify: `tests/agentkit/runtime/test_hook_runtime.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/agentkit/runtime/test_hook_runtime.py`:

```python
from agentkit.errors import HookTypeError
from agentkit.runtime.hookspecs import HOOK_SPECS
from agentkit.directive.types import Approve, Directive


class BadReturnPlugin:
    state_key = "bad_return"

    def hooks(self):
        return {"approve_tool_call": self.approve_tool_call}

    def approve_tool_call(self, **kwargs):
        return {"approved": True}  # Wrong: should be Directive subclass


class GoodDirectivePlugin:
    state_key = "good_directive"

    def hooks(self):
        return {"approve_tool_call": self.approve_tool_call}

    def approve_tool_call(self, **kwargs):
        return Approve()


class BadTuplePlugin:
    state_key = "bad_tuple"

    def hooks(self):
        return {"resolve_context_window": self.resolve}

    def resolve(self, **kwargs):
        return "not a tuple"


class TestHookRuntimeTypeValidation:
    @pytest.fixture
    def registry(self):
        return PluginRegistry()

    @pytest.fixture
    def runtime(self, registry):
        return HookRuntime(registry, specs=HOOK_SPECS)

    def test_call_first_raises_on_wrong_return_type(self, registry, runtime):
        registry.register(BadReturnPlugin())
        with pytest.raises(HookTypeError, match="approve_tool_call"):
            runtime.call_first("approve_tool_call", tool_name="bash", arguments={})

    def test_call_first_accepts_correct_directive(self, registry, runtime):
        registry.register(GoodDirectivePlugin())
        result = runtime.call_first("approve_tool_call", tool_name="bash", arguments={})
        assert isinstance(result, Approve)

    def test_call_first_accepts_none_even_with_return_type(self, registry, runtime):
        registry.register(NonePlugin())
        result = runtime.call_first("provide_llm")
        assert result is None

    def test_call_first_skips_validation_for_unknown_hook(self, registry, runtime):
        class CustomPlugin:
            state_key = "custom"
            def hooks(self):
                return {"my_custom_hook": lambda **kw: "anything"}
        registry.register(CustomPlugin())
        result = runtime.call_first("my_custom_hook")
        assert result == "anything"

    def test_call_first_validates_tuple_return(self, registry, runtime):
        registry.register(BadTuplePlugin())
        with pytest.raises(HookTypeError, match="resolve_context_window"):
            runtime.call_first("resolve_context_window", tape=None)

    def test_call_many_validates_each_result(self, registry, runtime):
        class BadContextPlugin:
            state_key = "bad_ctx"
            def hooks(self):
                return {"build_context": self.build_context}
            def build_context(self, **kwargs):
                return "not a list"
        registry.register(BadContextPlugin())
        with pytest.raises(HookTypeError, match="build_context"):
            runtime.call_many("build_context", tape=None)

    def test_call_many_accepts_correct_list(self, registry, runtime):
        class GoodContextPlugin:
            state_key = "good_ctx"
            def hooks(self):
                return {"build_context": self.build_context}
            def build_context(self, **kwargs):
                return [{"role": "system", "content": "memory"}]
        registry.register(GoodContextPlugin())
        results = runtime.call_many("build_context", tape=None)
        assert len(results) == 1

    def test_hook_runtime_no_specs_skips_validation(self, registry):
        runtime_no_specs = HookRuntime(registry)
        registry.register(BadReturnPlugin())
        result = runtime_no_specs.call_first("approve_tool_call", tool_name="bash", arguments={})
        assert result == {"approved": True}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agentkit/runtime/test_hook_runtime.py::TestHookRuntimeTypeValidation -v`
Expected: FAIL — `HookRuntime.__init__()` doesn't accept `specs` parameter

- [ ] **Step 3: Write minimal implementation**

Replace `src/agentkit/runtime/hook_runtime.py`:

```python
"""HookRuntime — the core dispatch engine for plugin hooks."""

from __future__ import annotations

import logging
from typing import Any

from agentkit.errors import HookError, HookTypeError
from agentkit.plugin.registry import PluginRegistry
from agentkit.runtime.hookspecs import HookSpec

logger = logging.getLogger(__name__)


class HookRuntime:
    def __init__(
        self,
        registry: PluginRegistry,
        specs: dict[str, HookSpec] | None = None,
    ) -> None:
        self._registry = registry
        self._specs = specs or {}

    def call_first(self, hook_name: str, **kwargs: Any) -> Any:
        callables = self._registry.get_hooks(hook_name)
        for fn in callables:
            try:
                result = fn(**kwargs)
                if result is not None:
                    self._validate_return(hook_name, result)
                    return result
            except (HookError, HookTypeError):
                raise
            except Exception as exc:
                raise HookError(str(exc), hook_name=hook_name) from exc
        return None

    def call_many(self, hook_name: str, **kwargs: Any) -> list[Any]:
        callables = self._registry.get_hooks(hook_name)
        results: list[Any] = []
        for fn in callables:
            try:
                result = fn(**kwargs)
                if result is not None:
                    self._validate_return(hook_name, result)
                    results.append(result)
            except (HookError, HookTypeError):
                raise
            except Exception as exc:
                raise HookError(str(exc), hook_name=hook_name) from exc
        return results

    def notify(self, hook_name: str, **kwargs: Any) -> None:
        callables = self._registry.get_hooks(hook_name)
        for fn in callables:
            try:
                fn(**kwargs)
            except Exception:
                logger.exception("Observer hook '%s' raised (swallowed)", hook_name)

    def _validate_return(self, hook_name: str, result: Any) -> None:
        spec = self._specs.get(hook_name)
        if spec is None or spec.return_type is None:
            return
        if not isinstance(result, spec.return_type):
            raise HookTypeError(
                f"Hook '{hook_name}' declared return_type={spec.return_type.__name__}, "
                f"got {type(result).__name__}: {repr(result)[:100]}",
                hook_name=hook_name,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agentkit/runtime/test_hook_runtime.py -v`
Expected: ALL PASS (both old `TestHookRuntime` with no specs, and new `TestHookRuntimeTypeValidation` with specs)

- [ ] **Step 5: Run full agentkit tests**

Run: `uv run pytest tests/agentkit/ -q --tb=short 2>&1 | tail -3`
Expected: ALL PASS — old `HookRuntime(registry)` calls still work (specs defaults to `{}`)

- [ ] **Step 6: Commit**

```bash
git add src/agentkit/runtime/hook_runtime.py tests/agentkit/runtime/test_hook_runtime.py
git commit -m "feat(hook_runtime): validate hook return types against HookSpec.return_type"
```

**QA Scenarios:**

```
Scenario: Wrong return type raises HookTypeError with hook name in message
  Tool: Bash (uv run pytest)
  Steps:
    1. Run: uv run pytest tests/agentkit/runtime/test_hook_runtime.py::TestHookRuntimeTypeValidation::test_call_first_raises_on_wrong_return_type -v
    2. Assert: exit code 0, PASSED
  Expected Result: HookTypeError raised, "approve_tool_call" in exception message
  Failure Indicators: No exception raised, or wrong exception type

Scenario: None return is always accepted even with return_type declared
  Tool: Bash (uv run pytest)
  Steps:
    1. Run: uv run pytest tests/agentkit/runtime/test_hook_runtime.py::TestHookRuntimeTypeValidation::test_call_first_accepts_none_even_with_return_type -v
    2. Assert: exit code 0, PASSED
  Expected Result: No HookTypeError for None returns
  Failure Indicators: HookTypeError raised for None

Scenario: No-specs HookRuntime passes through bad types (backward compat)
  Tool: Bash (uv run pytest)
  Steps:
    1. Run: uv run pytest tests/agentkit/runtime/test_hook_runtime.py::TestHookRuntimeTypeValidation::test_hook_runtime_no_specs_skips_validation -v
    2. Assert: exit code 0, PASSED
  Expected Result: Bad return type passes through when no specs injected
  Failure Indicators: HookTypeError raised when it shouldn't be
```

---

### Task 4: Wire HOOK_SPECS into HookRuntime at __main__.py

**Files:**
- Modify: `src/coding_agent/__main__.py` (line ~148: `runtime = HookRuntime(registry)`)
- Modify: `tests/coding_agent/test_cli_pipeline.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/coding_agent/test_cli_pipeline.py`:

```python
def test_hook_runtime_has_specs():
    from coding_agent.__main__ import create_agent
    pipeline, _ctx = create_agent(api_key="sk-test")
    runtime = pipeline._runtime
    assert len(runtime._specs) == 14
    assert "approve_tool_call" in runtime._specs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/coding_agent/test_cli_pipeline.py::test_hook_runtime_has_specs -v`
Expected: FAIL — `runtime._specs` is `{}` (empty dict)

- [ ] **Step 3: Write minimal implementation**

In `src/coding_agent/__main__.py`, find:

```python
runtime = HookRuntime(registry)
```

Change to:

```python
from agentkit.runtime.hookspecs import HOOK_SPECS
runtime = HookRuntime(registry, specs=HOOK_SPECS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/coding_agent/test_cli_pipeline.py::test_hook_runtime_has_specs -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -q --tb=short 2>&1 | tail -3`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/coding_agent/__main__.py tests/coding_agent/test_cli_pipeline.py
git commit -m "feat: wire HOOK_SPECS into HookRuntime for return type enforcement"
```

**QA Scenarios:**

```
Scenario: create_agent() produces a runtime with 14 specs
  Tool: Bash (uv run pytest)
  Steps:
    1. Run: uv run pytest tests/coding_agent/test_cli_pipeline.py::test_hook_runtime_has_specs -v
    2. Assert: exit code 0, PASSED
  Expected Result: runtime._specs has 14 entries including approve_tool_call
  Failure Indicators: len(runtime._specs) == 0

Scenario: Full test suite still passes after wiring
  Tool: Bash (uv run pytest)
  Steps:
    1. Run: uv run pytest tests/ -q 2>&1 | tail -3
    2. Assert: exit code 0, 0 failures
  Expected Result: No regressions from injecting specs
  Failure Indicators: Any test failure (especially existing pipeline tests)
```

---

### Task 5: Plugin registration unknown hook warning

**Files:**
- Modify: `src/agentkit/plugin/registry.py`
- Create or Modify: `tests/agentkit/plugin/test_registry.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/agentkit/plugin/test_registry.py` (or add to existing):

```python
import warnings
import pytest
from agentkit.plugin.registry import PluginRegistry
from agentkit.errors import PluginError
from agentkit.runtime.hookspecs import HOOK_SPECS


class GoodPlugin:
    state_key = "good"

    def hooks(self):
        return {"approve_tool_call": self.approve}

    def approve(self, tool_name="", arguments=None, **kwargs):
        return None


class UnknownHookPlugin:
    state_key = "unknown_hook"

    def hooks(self):
        return {"my_custom_hook": self.custom}

    def custom(self, **kwargs):
        return None


class TestRegistryUnknownHookWarning:
    @pytest.fixture
    def registry(self):
        return PluginRegistry(specs=HOOK_SPECS)

    def test_good_plugin_registers_without_warning(self, registry):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            registry.register(GoodPlugin())
        assert len(w) == 0
        assert "good" in registry.plugin_ids()

    def test_unknown_hook_name_produces_warning(self, registry):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            registry.register(UnknownHookPlugin())
        assert len(w) == 1
        assert "my_custom_hook" in str(w[0].message)
        assert issubclass(w[0].category, UserWarning)
        assert "unknown_hook" in registry.plugin_ids()

    def test_unknown_hook_plugin_still_registers_successfully(self, registry):
        registry.register(UnknownHookPlugin())
        assert "unknown_hook" in registry.plugin_ids()

    def test_registry_without_specs_skips_validation(self):
        registry = PluginRegistry()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            registry.register(UnknownHookPlugin())
        assert len(w) == 0
        assert "unknown_hook" in registry.plugin_ids()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agentkit/plugin/test_registry.py::TestRegistryUnknownHookWarning -v`
Expected: FAIL — `PluginRegistry.__init__()` doesn't accept `specs` parameter

- [ ] **Step 3: Write minimal implementation**

In `src/agentkit/plugin/registry.py`, modify `__init__` and `register`:

```python
"""PluginRegistry — manages plugin registration and hook lookup."""

from __future__ import annotations

import warnings
from typing import Any, Callable

from agentkit.errors import PluginError
from agentkit.plugin.protocol import Plugin


class PluginRegistry:
    def __init__(self, specs: dict | None = None) -> None:
        self._plugins: dict[str, Plugin] = {}
        self._hook_index: dict[str, list[Callable[..., Any]]] = {}
        self._specs = specs

    def register(self, plugin: Plugin) -> None:
        if not isinstance(plugin, Plugin):
            raise PluginError(
                f"{type(plugin).__name__} does not satisfy Plugin protocol",
                plugin_id=getattr(plugin, "state_key", "<unknown>"),
            )
        key = plugin.state_key
        if key in self._plugins:
            raise PluginError(f"duplicate state_key '{key}'", plugin_id=key)
        self._plugins[key] = plugin
        for hook_name, hook_fn in plugin.hooks().items():
            self._hook_index.setdefault(hook_name, []).append(hook_fn)
            if self._specs is not None and hook_name not in self._specs:
                warnings.warn(
                    f"Plugin '{key}' registered unknown hook '{hook_name}' "
                    f"(not in HookSpec registry)",
                    UserWarning,
                    stacklevel=2,
                )

    def plugin_ids(self) -> list[str]:
        return list(self._plugins.keys())

    def get(self, plugin_id: str) -> Plugin:
        if plugin_id not in self._plugins:
            raise PluginError(f"plugin '{plugin_id}' not found", plugin_id=plugin_id)
        return self._plugins[plugin_id]

    def get_hooks(self, hook_name: str) -> list[Callable[..., Any]]:
        return list(self._hook_index.get(hook_name, []))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agentkit/plugin/test_registry.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -q --tb=short 2>&1 | tail -3`
Expected: ALL PASS — old `PluginRegistry()` calls still work (`specs` defaults to `None`)

- [ ] **Step 6: Commit**

```bash
git add src/agentkit/plugin/registry.py tests/agentkit/plugin/test_registry.py
git commit -m "feat(registry): warn on unknown hook names at registration time"
```

**QA Scenarios:**

```
Scenario: Unknown hook triggers warning but does not block registration
  Tool: Bash (uv run pytest)
  Steps:
    1. Run: uv run pytest tests/agentkit/plugin/test_registry.py::TestRegistryUnknownHookWarning::test_unknown_hook_name_produces_warning -v
    2. Assert: exit code 0, PASSED
  Expected Result: UserWarning emitted with hook name, plugin still in registry
  Failure Indicators: Test fails, or PluginError raised

Scenario: Known hooks produce no warnings
  Tool: Bash (uv run pytest)
  Steps:
    1. Run: uv run pytest tests/agentkit/plugin/test_registry.py::TestRegistryUnknownHookWarning::test_good_plugin_registers_without_warning -v
    2. Assert: exit code 0, PASSED
  Expected Result: No UserWarning for known hook names
  Failure Indicators: Spurious warnings for valid hooks
```

---

### Task 6: Wire specs into PluginRegistry at __main__.py

**Files:**
- Modify: `src/coding_agent/__main__.py`
- Modify: `tests/coding_agent/test_cli_pipeline.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/coding_agent/test_cli_pipeline.py`:

```python
from agentkit.runtime.hookspecs import HOOK_SPECS


def test_plugin_registry_has_specs():
    from coding_agent.__main__ import create_agent
    pipeline, _ctx = create_agent(api_key="sk-test")
    registry = pipeline._registry
    assert registry._specs == HOOK_SPECS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/coding_agent/test_cli_pipeline.py::test_plugin_registry_has_specs -v`
Expected: FAIL — `registry._specs` is `None`, so the equality assertion fails

- [ ] **Step 3: Write minimal implementation**

In `src/coding_agent/__main__.py`, find:

```python
registry = PluginRegistry()
```

Change to:

```python
from agentkit.runtime.hookspecs import HOOK_SPECS
registry = PluginRegistry(specs=HOOK_SPECS)
```

Note: `HOOK_SPECS` import may already exist from Task 4. Do not duplicate it — consolidate into a single import at the top of `create_agent()`.

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest tests/ -q --tb=short 2>&1 | tail -3`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/__main__.py tests/coding_agent/test_cli_pipeline.py
git commit -m "feat: wire HOOK_SPECS into PluginRegistry for registration-time warnings"
```

**QA Scenarios:**

```
Scenario: create_agent() produces registry with 14 specs
  Tool: Bash (uv run pytest)
  Steps:
    1. Run: uv run pytest tests/coding_agent/test_cli_pipeline.py::test_plugin_registry_has_specs -v
    2. Assert: exit code 0, PASSED
  Expected Result: registry._specs has 14 entries
  Failure Indicators: len == 0

Scenario: No spurious warnings from existing plugins at startup
  Tool: Bash
  Steps:
    1. Run: uv run python -W error::UserWarning -c "from coding_agent.__main__ import create_agent; create_agent(api_key='sk-test'); print('ok')" 2>&1
    2. Assert: output is "ok", no warnings about unknown hooks for known plugins
  Expected Result: Clean startup, no unknown hook warnings for LLMProviderPlugin etc.
  Failure Indicators: UserWarning raised, "unknown hook" message for a known plugin
```

---

### Task 7: Pipeline isinstance guards at critical consumption sites

**Files:**
- Modify: `src/agentkit/runtime/pipeline.py`
- Create: `tests/agentkit/runtime/test_pipeline_typeguards.py`

**⚠️ CRITICAL — P3 preservation:**
`_stage_build_context` was rewritten in P3 to pass `window_start` to `handoff()` and add `ctx._handoff_done`. The guard added here must be inserted **around** the existing P3 logic, not replace it. Specifically:
- Keep `abs_window_start = ctx.tape.window_start + window_start`
- Keep `ctx._handoff_done = True`
- Keep `not ctx._handoff_done` guard
- ADD the `isinstance(window_result, tuple) and len==2 and isinstance(window_result[0], int)` structural check wrapping the existing logic

- [ ] **Step 1: Write the failing tests**

Create `tests/agentkit/runtime/test_pipeline_typeguards.py`:

```python
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from agentkit.runtime.pipeline import Pipeline, PipelineContext
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.plugin.registry import PluginRegistry
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry
from agentkit.directive.types import Approve, Directive


def _make_tape(n=5):
    t = Tape()
    for i in range(n):
        t.append(Entry(kind="message", payload={"role": "user", "content": f"msg {i}"}))
    return t


class TestPipelineTypeGuards:
    def test_build_context_skips_non_tuple_window_result(self):
        registry = PluginRegistry()

        class BadWindowPlugin:
            state_key = "bad_window"
            def hooks(self):
                return {"resolve_context_window": self.resolve}
            def resolve(self, **kwargs):
                return "not a tuple"

        registry.register(BadWindowPlugin())
        runtime = HookRuntime(registry)  # No specs → no HookTypeError here
        pipeline = Pipeline(runtime=runtime, registry=registry)

        tape = _make_tape()
        ctx = PipelineContext(tape=tape, config={"system_prompt": "test"})

        asyncio.get_event_loop().run_until_complete(pipeline._stage_build_context(ctx))
        assert len(ctx.tape.windowed_entries()) == 5  # Tape unchanged

    def test_build_context_skips_tuple_wrong_length(self):
        registry = PluginRegistry()

        class ShortTuplePlugin:
            state_key = "short_tuple"
            def hooks(self):
                return {"resolve_context_window": self.resolve}
            def resolve(self, **kwargs):
                return (1,)  # len 1, not 2

        registry.register(ShortTuplePlugin())
        runtime = HookRuntime(registry)
        pipeline = Pipeline(runtime=runtime, registry=registry)

        tape = _make_tape()
        ctx = PipelineContext(tape=tape, config={"system_prompt": "test"})

        asyncio.get_event_loop().run_until_complete(pipeline._stage_build_context(ctx))
        assert len(ctx.tape.windowed_entries()) == 5

    def test_build_context_skips_tuple_non_int_first(self):
        registry = PluginRegistry()

        class BadFirstPlugin:
            state_key = "bad_first"
            def hooks(self):
                return {"resolve_context_window": self.resolve}
            def resolve(self, **kwargs):
                return ("not_int", None)  # First element is not int

        registry.register(BadFirstPlugin())
        runtime = HookRuntime(registry)
        pipeline = Pipeline(runtime=runtime, registry=registry)

        tape = _make_tape()
        ctx = PipelineContext(tape=tape, config={"system_prompt": "test"})

        asyncio.get_event_loop().run_until_complete(pipeline._stage_build_context(ctx))
        assert len(ctx.tape.windowed_entries()) == 5

    def test_render_skips_non_directive(self):
        registry = PluginRegistry()

        class BadTurnEndPlugin:
            state_key = "bad_turn"
            def hooks(self):
                return {"on_turn_end": self.on_turn_end}
            def on_turn_end(self, **kwargs):
                return {"not": "a directive"}

        registry.register(BadTurnEndPlugin())
        runtime = HookRuntime(registry)

        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock(return_value=None)

        pipeline = Pipeline(runtime=runtime, registry=registry, directive_executor=mock_executor)
        ctx = PipelineContext(tape=_make_tape())

        asyncio.get_event_loop().run_until_complete(pipeline._stage_render(ctx))
        mock_executor.execute.assert_not_called()

    def test_render_stores_only_valid_directives_in_output(self):
        registry = PluginRegistry()

        class MixedPlugin:
            state_key = "mixed"
            def hooks(self):
                return {"on_turn_end": self.on_turn_end}
            def on_turn_end(self, **kwargs):
                return {"bad": "dict"}  # Will be filtered

        registry.register(MixedPlugin())
        runtime = HookRuntime(registry)
        pipeline = Pipeline(runtime=runtime, registry=registry)
        ctx = PipelineContext(tape=_make_tape())

        asyncio.get_event_loop().run_until_complete(pipeline._stage_render(ctx))
        assert ctx.output == {"directives": []}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agentkit/runtime/test_pipeline_typeguards.py -v`
Expected: FAIL — pipeline currently passes bad values through without guards

- [ ] **Step 3: Write minimal implementation**

Add import at top of `src/agentkit/runtime/pipeline.py`:

```python
from agentkit.directive.types import Directive
```

**Modify `_stage_build_context`** — add structural validation wrapping the existing P3 logic:

```python
    async def _stage_build_context(self, ctx: PipelineContext) -> None:
        window_result = self._runtime.call_first(
            "resolve_context_window", tape=ctx.tape
        )
        if window_result is not None:
            # Structural validation: must be (int, Entry|None) tuple
            if (
                not isinstance(window_result, tuple)
                or len(window_result) != 2
                or not isinstance(window_result[0], int)
            ):
                logger.error(
                    "resolve_context_window returned invalid shape %s — "
                    "expected (int, Entry|None) tuple, skipping",
                    type(window_result).__name__,
                )
            else:
                window_start, summary_anchor = window_result
                if summary_anchor is not None and not ctx._handoff_done:
                    abs_window_start = ctx.tape.window_start + window_start
                    ctx.tape.handoff(summary_anchor, window_start=abs_window_start)
                    ctx._handoff_done = True
                logger.info(
                    "Context window advanced: %d entries visible (of %d total)",
                    len(ctx.tape.windowed_entries()),
                    len(ctx.tape),
                )
        else:
            summary = self._runtime.call_first("summarize_context", tape=ctx.tape)
            if summary is not None:
                ctx.tape = Tape(
                    entries=list(summary),
                    tape_id=ctx.tape.tape_id,
                    parent_id=ctx.tape.parent_id,
                )
                logger.info(
                    "Context summarized (legacy): %d entries remaining", len(ctx.tape)
                )

        grounding_results = self._runtime.call_many("build_context", tape=ctx.tape)
        grounding: list[dict[str, Any]] = []
        for result in grounding_results:
            if isinstance(result, list):
                grounding.extend(result)

        from agentkit.context.builder import ContextBuilder

        system_prompt = ctx.config.get("system_prompt", "You are a helpful assistant.")
        builder = ContextBuilder(system_prompt=system_prompt)
        ctx.messages = builder.build(
            ctx.tape,
            grounding=grounding or None,
            entries=ctx.tape.windowed_entries() if ctx.tape.window_start > 0 else None,
        )
```

**Modify `_stage_render`** — filter non-Directive values before storing and executing:

```python
    async def _stage_render(self, ctx: PipelineContext) -> None:
        raw_directives = self._runtime.call_many("on_turn_end", tape=ctx.tape)

        valid_directives = []
        for d in raw_directives:
            if d is None:
                continue
            if not isinstance(d, Directive):
                logger.error(
                    "on_turn_end returned %s instead of Directive — skipping",
                    type(d).__name__,
                )
                continue
            valid_directives.append(d)

        ctx.output = {"directives": valid_directives}

        if self._directive_executor is not None:
            for directive in valid_directives:
                await self._directive_executor.execute(directive)
```

**Modify `approve_tool_call` guard** in `_stage_run_model` (around line 270) — **fail closed**:

```python
                    approved = True
                    if directive is not None:
                        if not isinstance(directive, Directive):
                            logger.error(
                                "approve_tool_call returned %s instead of Directive "
                                "— rejecting (fail closed)",
                                type(directive).__name__,
                            )
                            approved = False
                        elif self._directive_executor is not None:
                            approved = await self._directive_executor.execute(directive)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agentkit/runtime/test_pipeline_typeguards.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -q --tb=short 2>&1 | tail -3`
Expected: ALL PASS — existing pipeline tests unaffected

- [ ] **Step 6: Commit**

```bash
git add src/agentkit/runtime/pipeline.py tests/agentkit/runtime/test_pipeline_typeguards.py
git commit -m "feat(pipeline): add isinstance guards at hook result consumption sites"
```

**QA Scenarios:**

```
Scenario: Non-tuple resolve_context_window is logged and skipped
  Tool: Bash (uv run pytest)
  Steps:
    1. Run: uv run pytest tests/agentkit/runtime/test_pipeline_typeguards.py::TestPipelineTypeGuards::test_build_context_skips_non_tuple_window_result -v
    2. Assert: exit code 0, PASSED
  Expected Result: Tape unchanged (5 entries), no crash
  Failure Indicators: TypeError unpacking, or handoff accidentally called

Scenario: Bad on_turn_end directive is filtered before executor sees it
  Tool: Bash (uv run pytest)
  Steps:
    1. Run: uv run pytest tests/agentkit/runtime/test_pipeline_typeguards.py::TestPipelineTypeGuards::test_render_skips_non_directive -v
    2. Assert: exit code 0, PASSED, mock_executor.execute.assert_not_called()
  Expected Result: Executor never called with bad dict
  Failure Indicators: executor.execute called with {"not": "a directive"}

Scenario: P3 window_start + _handoff_done logic preserved
  Tool: Bash (uv run pytest)
  Steps:
    1. Run: uv run pytest tests/agentkit/runtime/test_pipeline.py -v -k "handoff or reentrant"
    2. Assert: exit code 0, all pass
  Expected Result: P3 handoff tests unaffected by Task 7 changes
  Failure Indicators: test_build_context_passes_window_start_to_handoff fails
```

---

### Task 8: Final integration — end-to-end type rejection test

**Files:**
- Modify: `tests/agentkit/runtime/test_pipeline_typeguards.py`

- [ ] **Step 1: Write end-to-end test**

Add to `tests/agentkit/runtime/test_pipeline_typeguards.py`:

```python
from agentkit.runtime.hookspecs import HOOK_SPECS
from agentkit.errors import HookTypeError


class TestEndToEndTypeEnforcement:
    def test_bad_directive_caught_by_runtime_before_pipeline(self):
        registry = PluginRegistry()

        class BadApprovalPlugin:
            state_key = "bad_approval"
            def hooks(self):
                return {"approve_tool_call": self.approve}
            def approve(self, **kwargs):
                return {"bad": "dict"}

        registry.register(BadApprovalPlugin())
        runtime = HookRuntime(registry, specs=HOOK_SPECS)

        with pytest.raises(HookTypeError, match="approve_tool_call"):
            runtime.call_first("approve_tool_call", tool_name="bash", arguments={})

    def test_good_plugin_passes_both_layers(self):
        registry = PluginRegistry(specs=HOOK_SPECS)

        class GoodApprovalPlugin:
            state_key = "good"
            def hooks(self):
                return {"approve_tool_call": self.approve}
            def approve(self, **kwargs):
                return Approve()

        registry.register(GoodApprovalPlugin())
        runtime = HookRuntime(registry, specs=HOOK_SPECS)

        result = runtime.call_first("approve_tool_call", tool_name="bash", arguments={})
        assert isinstance(result, Approve)

    def test_unknown_hook_has_no_validation(self):
        registry = PluginRegistry(specs=HOOK_SPECS)

        class ExtensionPlugin:
            state_key = "ext"
            def hooks(self):
                return {"custom_analysis": self.analyze}
            def analyze(self, **kwargs):
                return {"anything": True}

        registry.register(ExtensionPlugin())
        runtime = HookRuntime(registry, specs=HOOK_SPECS)
        result = runtime.call_first("custom_analysis")
        assert result == {"anything": True}

    def test_none_always_allowed_with_specs(self):
        registry = PluginRegistry(specs=HOOK_SPECS)

        class NoneApprovalPlugin:
            state_key = "none_approval"
            def hooks(self):
                return {"approve_tool_call": self.approve}
            def approve(self, **kwargs):
                return None

        registry.register(NoneApprovalPlugin())
        runtime = HookRuntime(registry, specs=HOOK_SPECS)
        result = runtime.call_first("approve_tool_call", tool_name="bash", arguments={})
        assert result is None
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/agentkit/runtime/test_pipeline_typeguards.py::TestEndToEndTypeEnforcement -v`
Expected: ALL PASS (all 4 focused integration tests)

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -q --tb=short 2>&1 | tail -3`
Expected: ALL PASS, total >= 866 + ~15 new tests

- [ ] **Step 4: Commit**

```bash
git add tests/agentkit/runtime/test_pipeline_typeguards.py
git commit -m "test: end-to-end type enforcement integration tests"
```

**QA Scenarios:**

```
Scenario: HookRuntime + PluginRegistry full two-layer defense
  Tool: Bash (uv run pytest)
  Steps:
    1. Run: uv run pytest tests/agentkit/runtime/test_pipeline_typeguards.py::TestEndToEndTypeEnforcement -v
    2. Assert: exit code 0, all 4 tests pass
  Expected Result: Bad type caught at runtime layer, good type passes, unknown hook is not runtime-validated (registration may warn), and `None` remains allowed
  Failure Indicators: Any assertion failure in the chain

Scenario: Total test count includes all P4 new tests
  Tool: Bash (uv run pytest)
  Steps:
    1. Run: uv run pytest tests/ -q 2>&1 | tail -3
    2. Assert: passed count >= 881 (866 baseline + 15 new)
    3. Assert: 0 failures
  Expected Result: All new tests added, none deleted, no regressions
  Failure Indicators: Count below 881, any failure
```

---

## Final Verification Wave

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results before completing.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read plan end-to-end. For each "Must Have": verify implementation exists (read file, run command). For each "Must NOT Have": search codebase for forbidden patterns. Check test counts.
  Output: `Must Have [N/9] | Must NOT Have [N/6] | Tests [N pass] | VERDICT: APPROVE/REJECT`

  **Note:** with both layers wired in `create_agent()`, bad typed directive returns are normally caught by `HookRuntime` before Pipeline sees them. The Pipeline `approve_tool_call` guard is still required as defense-in-depth for no-specs or legacy call paths, so verify that guard structurally in source.

  **Key checks:**
  - `HookRuntime(registry, specs=HOOK_SPECS)` raises `HookTypeError` on wrong type → `uv run pytest tests/agentkit/runtime/test_hook_runtime.py::TestHookRuntimeTypeValidation -v`
  - `HookRuntime(registry)` (no specs) works identically to before → `uv run pytest tests/agentkit/runtime/test_hook_runtime.py::TestHookRuntime -v`
  - `execute_tools_batch` in HOOK_SPECS → `uv run python -c "from agentkit.runtime.hookspecs import HOOK_SPECS; assert 'execute_tools_batch' in HOOK_SPECS; print('ok')"`
  - Pipeline `approve_tool_call` guard is fail-closed → `grep -A 8 "not isinstance(directive, Directive)" src/agentkit/runtime/pipeline.py`
  - Pipeline `_stage_render` filters non-Directive → `uv run pytest tests/agentkit/runtime/test_pipeline_typeguards.py -v -k "render"`
  - agentkit does NOT import coding_agent → `grep -rn "from coding_agent\|import coding_agent" src/agentkit/ || true`
  - `ctx._handoff_done` preserved without extra Task-7-only duplication → `grep -n "_handoff_done" src/agentkit/runtime/pipeline.py`
  - Both `HookRuntime` and `PluginRegistry` wired in `__main__.py` → `grep -n "HOOK_SPECS" src/coding_agent/__main__.py`

- [ ] F2. **Code Quality Review** — `oracle`
  Run mypy on the changed `agentkit` files. Review for: empty excepts, unused imports, overcomplicated isinstance chains, hardcoded magic values. Check P3 preservation in `_stage_build_context`. Validate the `__main__.py` wiring through targeted pytest instead of mypy because that file already carries unrelated pre-existing mypy noise.
  Output: `Mypy [PASS/FAIL] | Tests [N/0] | Files [N clean] | P3 preserved [YES/NO] | VERDICT: APPROVE/REJECT`

  **Key checks:**
  ```bash
  uv run mypy src/agentkit/errors.py src/agentkit/runtime/hookspecs.py src/agentkit/runtime/hook_runtime.py src/agentkit/plugin/registry.py src/agentkit/runtime/pipeline.py --no-error-summary
  uv run pytest tests/coding_agent/test_cli_pipeline.py -q -k "hook_runtime_has_specs or plugin_registry_has_specs"
  grep -n "TODO\|FIXME\|HACK" src/agentkit/runtime/hookspecs.py src/agentkit/runtime/hook_runtime.py src/agentkit/plugin/registry.py
  grep -n "abs_window_start\|_handoff_done\|window_start=" src/agentkit/runtime/pipeline.py
  ```

- [ ] F3. **Real Manual QA** — `unspecified-high`
  Execute EVERY QA scenario from EVERY task. Save evidence to `.sisyphus/evidence/p4-final-qa/`. Test cross-layer integration (bad plugin → HookTypeError at runtime → never reaches pipeline).
  Output: `Scenarios [N/N pass] | Integration [PASS/FAIL] | VERDICT: APPROVE/REJECT`

  **Cross-layer integration test:**
  ```bash
  uv run python -c "
  from agentkit.plugin.registry import PluginRegistry
  from agentkit.runtime.hook_runtime import HookRuntime
  from agentkit.runtime.hookspecs import HOOK_SPECS
  from agentkit.errors import HookTypeError

  class BadPlugin:
      state_key = 'bad'
      def hooks(self): return {'approve_tool_call': lambda **kw: {'bad': 'dict'}}

  r = PluginRegistry(specs=HOOK_SPECS)
  r.register(BadPlugin())
  rt = HookRuntime(r, specs=HOOK_SPECS)
  try:
      rt.call_first('approve_tool_call', tool_name='bash', arguments={})
      print('FAIL: no exception raised')
  except HookTypeError as e:
      print(f'PASS: caught HookTypeError: {e}')
  "
  ```

- [ ] F4. **Scope Fidelity Check** — `oracle`
  For each task: read "What to do", read actual diff. Verify 1:1. Check no P3 regressions. Verify commit file sets match plan.
  Output: `Tasks [N/8 compliant] | P3 regressions [0] | Must NOT violations [0] | VERDICT: APPROVE/REJECT`

  **Key checks:**
  ```bash
  # Verify P3 window_start logic still present
  grep -n "abs_window_start\|window_start=abs_window_start" src/agentkit/runtime/pipeline.py

  # Verify P3 _handoff_done appears only in the preserved declaration + guard + assignment
  grep -n "_handoff_done" src/agentkit/runtime/pipeline.py

  # Verify approve_tool_call guard is fail-closed (not auto-approve on bad type)
  grep -A 8 "not isinstance(directive, Directive)" src/agentkit/runtime/pipeline.py

  # Verify no coding_agent import in agentkit
  grep -rn "from coding_agent\|import coding_agent" src/agentkit/ || true
  ```

---

## Commit Strategy

| Task | Message | Files |
|------|---------|-------|
| 1 | `feat(errors): add HookTypeError for hook return type validation` | errors.py, test_hook_runtime.py |
| 2 | `feat(hookspecs): add return_type field + execute_tools_batch to HookSpec` | hookspecs.py, test_hookspecs.py |
| 3 | `feat(hook_runtime): validate hook return types against HookSpec.return_type` | hook_runtime.py, test_hook_runtime.py |
| 4 | `feat: wire HOOK_SPECS into HookRuntime for return type enforcement` | __main__.py, test_cli_pipeline.py |
| 5 | `feat(registry): warn on unknown hook names at registration time` | registry.py, test_registry.py |
| 6 | `feat: wire HOOK_SPECS into PluginRegistry for registration-time warnings` | __main__.py, test_cli_pipeline.py |
| 7 | `feat(pipeline): add isinstance guards at hook result consumption sites` | pipeline.py, test_pipeline_typeguards.py |
| 8 | `test: end-to-end type enforcement integration tests` | test_pipeline_typeguards.py |

## Success Criteria

```bash
uv run pytest tests/ -x -q --tb=short  # ALL PASS, 0 failures
```

- [x] HookRuntime raises HookTypeError when hook returns wrong type (with specs wired)
- [x] HookRuntime without specs works exactly as before (backward compat)
- [x] PluginRegistry warns on unknown hook names (with specs wired)
- [x] PluginRegistry without specs works exactly as before (backward compat)
- [x] Pipeline guards at 3 consumption sites: `resolve_context_window` structural check, `approve_tool_call` **fail closed**, `on_turn_end` filter-before-store
- [x] `execute_tools_batch` is in HOOK_SPECS (no spurious warnings from ParallelExecutorPlugin)
- [x] `approve_tool_call` guard rejects (not auto-approves) on invalid return type
- [x] P3 `abs_window_start` + `ctx._handoff_done` logic preserved in `_stage_build_context`
- [x] Total test count >= 866 + ~15 new tests
- [x] agentkit does NOT import coding_agent

## Closure Note — 2026-04-10

- Fresh closure verification passed:
  - `uv run pytest tests/ -v` → `1623 passed, 31 warnings`
  - `uv run mypy src/agentkit/runtime/hook_runtime.py src/agentkit/runtime/hookspecs.py src/agentkit/plugin/registry.py src/agentkit/errors.py src/agentkit/runtime/pipeline.py` → success, 0 issues
- Closure was briefly blocked by `tests/integration/test_wire_http_integration.py::TestPromptStreamingFlow::test_prompt_streaming_events`.
- The blocker was not missing P4 functionality. It was a non-hermetic HTTP streaming integration test that expected `StreamDelta` while relying on the default real-provider path in an environment without provider credentials.
- The test was made deterministic by injecting `MockProvider()` into the HTTP-created session inside that integration test, preserving the separate contract checked in `tests/ui/test_http_server.py` that HTTP session creation defaults to `session.provider is None`.
- Closure evidence: `.sisyphus/evidence/p4-closure-2026-04-10.txt`
