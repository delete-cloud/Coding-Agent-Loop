# CLI Cutover: Pipeline Replaces AgentLoop

## TL;DR

> **Quick Summary**: Replace the old `AgentLoop` orchestration with the new `agentkit.Pipeline` across all CLI entry points (`run`, `repl`, headless), bridging the streaming/UI gap via an adapter layer that keeps agentkit general-purpose.
> 
> **Deliverables**:
> - Streaming event callback mechanism in agentkit Pipeline (general-purpose, no wire coupling)
> - `PipelineAdapter` in `coding_agent/` bridging Pipeline → WireMessage + TurnOutcome for CLI
> - `run` command using PipelineAdapter with TUI streaming
> - `repl` command using PipelineAdapter with multi-turn context + interactive approval
> - Headless mode using PipelineAdapter with stdout streaming
> - Doom detection, parallel tool execution, result truncation, metrics as Pipeline plugins
> - Provider event adapter bridging old StreamEvent → new TextEvent/ToolCallEvent/DoneEvent
> - All 29 failing legacy tests migrated to new interfaces
> - Old stack dead code removed (core/loop.py, core/context.py, core/tape.py, etc.)
> 
> **Estimated Effort**: Large (5 phases, ~23 tasks + T6b tool parity)
> **Parallel Execution**: YES — 4 waves + final verification
> **Critical Path**: T1→T3→T7→T10→T13→T17→T20→Final

---

## Context

### Original Request
User requested an implementation plan ("给个实现计划") for CLI cutover — making the user-facing CLI use the new agentkit Pipeline instead of the old AgentLoop. This follows the successful merge of PR #16 (agentkit-v1, 23 tasks, 231 tests).

### Interview Summary
**Key Discussions**:
- Two parallel stacks exist: new agentkit (working, untouched by CLI) and old AgentLoop (active in CLI)
- 6 feature gaps identified: streaming, doom detection, parallel execution, result truncation, metrics, interactive approval
- User confirmed CLI cutover as next step direction
- User follows strict TDD (RED-GREEN-REFACTOR)
- User wants general-purpose agent positioning (not just coding)
- User must approve architectural decisions

**Research Findings**:
- `RichConsumer.emit()` only pattern-matches on **3 WireMessage types**: `TurnEnd`, `StreamDelta`, `ToolCallDelta` — others are silently ignored, simplifying streaming requirements
- `DirectiveExecutor` supports `AskUser` directive with `ask_user_handler` — interactive approval is feasible
- Tape implementations are **incompatible** (old: int id + str timestamp, new: str id + float timestamp) — must use new Tape exclusively
- Provider event types are **incompatible** (old: `StreamEvent` with type field, new: `TextEvent`/`ToolCallEvent`/`DoneEvent` classes) — adapter needed
- New tools are **mostly complete** but missing `subagent` tool and `file_patch` in CoreToolsPlugin — added as Task 6b (Wave 1) for CLI parity
- Pipeline `_stage_run_model` has **no try/except around execute_tool** — error handling must be added

### Metis Review
**Identified Gaps** (addressed):
- Streaming architecture decision (callback vs async generator) → Default: callback `on_event` on PipelineContext
- Subagent tool missing from new stack → Added to CoreToolsPlugin in Task 6b (Wave 1)
- Intermediate state strategy during cutover → `USE_PIPELINE=1` env var toggle
- Tape incompatibility between stacks → Use new agentkit Tape exclusively
- Provider event incompatibility → Build adapter in LLMProviderPlugin
- Error recovery in REPL (tape rollback loses user message) → PipelineAdapter preserves user message
- Pipeline lacks tool result truncation → Add as hook/plugin
- Pipeline lacks tool execution error handling → Add try/except in execute_tool path

---

## Work Objectives

### Core Objective
Replace the old `AgentLoop` orchestration with agentkit `Pipeline` across all CLI entry points, while keeping agentkit general-purpose (no wire protocol coupling) and maintaining feature parity with the old stack.

### Concrete Deliverables
- `src/agentkit/runtime/pipeline.py` — enhanced with `on_event` streaming callback
- `src/coding_agent/adapter.py` — new file: `PipelineAdapter` bridging Pipeline → CLI interface
- `src/coding_agent/cli/repl.py` — refactored to use PipelineAdapter
- `src/coding_agent/__main__.py` — refactored `run`/`repl` commands to use PipelineAdapter
- `src/coding_agent/plugins/doom_detector.py` — new file: doom detection as agentkit plugin
- `src/coding_agent/plugins/parallel_executor.py` — new file: parallel tool execution plugin
- `src/coding_agent/plugins/metrics.py` — new file: session metrics plugin
- Updated `src/coding_agent/plugins/llm_provider.py` — provider event adapter
- All tests in `tests/tools/` and `tests/providers/` passing
- Old stack code in `src/coding_agent/core/` removed (loop.py, context.py, tape.py)

### Definition of Done
- [ ] `uv run pytest --no-header -q` → 0 failures, ≥700 tests
- [ ] `uv run python -m coding_agent run --goal "echo hello"` → executes via Pipeline, TUI streams in real-time
- [ ] `uv run python -m coding_agent repl` → multi-turn works, streaming works, context retained
- [ ] `grep -r "from coding_agent.core.loop" src/` → empty (no old AgentLoop imports)
- [ ] `grep -r "from coding_agent.core.tape" src/` → empty (no old Tape imports)
- [ ] No `agentkit` module imports from `coding_agent.wire` (general-purpose boundary)

### Must Have
- Streaming events visible in TUI during LLM response (not buffered until turn end)
- Multi-turn REPL with context preservation across turns
- Interactive approval in REPL mode (user can approve/deny tool calls)
- Headless mode printing to stdout
- Tool result truncation preventing context window blowup
- Doom loop detection preventing infinite loops
- Feature toggle (`USE_PIPELINE=1`) during transition for rollback safety
- All existing 231 agentkit tests continue passing
- All legacy test failures resolved (migrated or replaced)

### Must NOT Have (Guardrails)
- **G1**: No `coding_agent.wire` imports inside `src/agentkit/` — agentkit stays general-purpose
- **G2**: No behavior changes in TUI output — identical rendering for same LLM responses
- **G3**: No old code deletion until feature parity verified (Phase 5 only)
- **G4**: No test deletion without replacement — migrate, don't delete
- **G5**: No scope expansion — no new tools, providers, or general-purpose features during cutover
- **G6**: No subagent rewrite — port existing subagent to CoreToolsPlugin (Task 6b) as thin wrapper around existing spawn logic
- **G7**: No premature abstraction — adapter is a thin bridge, not an event bus or pub/sub system
- **G8**: No k8s/deployment work during cutover (separate effort)

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: YES (uv run pytest, 700+ tests)
- **Automated tests**: YES (TDD — RED-GREEN-REFACTOR)
- **Framework**: pytest + unittest.mock.AsyncMock
- **Each task follows**: RED (failing test) → GREEN (minimal impl) → REFACTOR

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **CLI/TUI**: Use interactive_bash (tmux) — Run command, send keystrokes, validate output
- **API/Module**: Use Bash (uv run pytest / uv run python -c) — Import, call functions, compare output
- **Integration**: Use Bash — Full end-to-end command execution with assertions

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — all independent, start immediately):
├── Task 1: Add on_event streaming callback to PipelineContext [quick]
├── Task 2: Provider event adapter (StreamEvent → TextEvent/ToolCallEvent/DoneEvent) [quick]
├── Task 3: Add tool execution error handling to Pipeline [quick]
├── Task 4: Add tool result truncation hook to Pipeline [quick]
├── Task 5: TurnOutcome dataclass for Pipeline results [quick]
├── Task 6: Feature toggle infrastructure (USE_PIPELINE env var) [quick]
└── Task 6b: Add file_patch + subagent tools to CoreToolsPlugin [unspecified-high]

Wave 2 (Core adapter + plugins — depends on Wave 1):
├── Task 7: Pipeline emits streaming events during run_model stage (depends: 1) [deep]
├── Task 8: DoomDetectorPlugin (depends: 1) [unspecified-high]
├── Task 9: ParallelExecutorPlugin (depends: 3, 4) [deep]
├── Task 10: PipelineAdapter: Pipeline → TurnOutcome + WireMessages (depends: 1, 2, 5, 7) [deep]
├── Task 11: SessionMetricsPlugin (depends: 1) [unspecified-high]
└── Task 12: REPL-safe error recovery in PipelineAdapter (depends: 5, 10) [unspecified-high]

Wave 3 (CLI cutover — depends on Wave 2):
├── Task 13: Wire `run` command to PipelineAdapter (depends: 6, 10) [unspecified-high]
├── Task 14: Wire `repl` command to PipelineAdapter (depends: 6, 10, 12) [deep]
├── Task 15: Wire headless mode to PipelineAdapter (depends: 6, 10) [unspecified-high]
├── Task 16: Interactive approval via DirectiveExecutor in REPL (depends: 14) [unspecified-high]
└── Task 17: Integration test: full run+repl via Pipeline (depends: 13, 14, 15) [deep]

Wave 4 (Cleanup — depends on Wave 3):
├── Task 18: Migrate failing tests/tools/ tests to new interfaces (depends: 17) [unspecified-high]
├── Task 19: Migrate failing tests/providers/ tests to new interfaces (depends: 17) [unspecified-high]
├── Task 20: Remove feature toggle, make Pipeline the only path (depends: 17, 18, 19) [quick]
├── Task 21: Remove old stack dead code (core/loop.py, etc.) (depends: 20) [quick]
└── Task 22: Final import cleanup and unused code sweep (depends: 21) [quick]

Wave FINAL (After ALL tasks — 4 parallel reviews, then user okay):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real manual QA (unspecified-high)
└── Task F4: Scope fidelity check (deep)
→ Present results → Get explicit user okay
```

Critical Path: T1 → T7 → T10 → T13/T14 → T17 → T18/T19 → T20 → T21 → Final
Parallel Speedup: ~60% faster than sequential
Max Concurrent: 7 (Wave 1)

### Dependency Matrix

| Task | Blocked By | Blocks |
|------|-----------|--------|
| T1 | — | T7, T8, T10, T11 |
| T2 | — | T10 |
| T3 | — | T9 |
| T4 | — | T9 |
| T5 | — | T10, T12 |
| T6 | — | T13, T14, T15 |
| T6b | — | T10, T13, T14, T15 |
| T7 | T1 | T10 |
| T8 | T1 | T17 |
| T9 | T3, T4 | T17 |
| T10 | T1, T2, T5, T6b, T7 | T12, T13, T14, T15 |
| T11 | T1 | T17 |
| T12 | T5, T10 | T14 |
| T13 | T6, T10 | T17 |
| T14 | T6, T10, T12 | T16, T17 |
| T15 | T6, T10 | T17 |
| T16 | T14 | T17 |
| T17 | T13, T14, T15, T16, T8, T9, T11 | T18, T19 |
| T18 | T17 | T20 |
| T19 | T17 | T20 |
| T20 | T17, T18, T19 | T21 |
| T21 | T20 | T22 |
| T22 | T21 | Final |

### Agent Dispatch Summary

- **Wave 1**: **7 tasks** — T1-T6 → `quick`, T6b → `unspecified-high`
- **Wave 2**: **6 tasks** — T7 → `deep`, T8 → `unspecified-high`, T9 → `deep`, T10 → `deep`, T11 → `unspecified-high`, T12 → `unspecified-high`
- **Wave 3**: **5 tasks** — T13 → `unspecified-high`, T14 → `deep`, T15 → `unspecified-high`, T16 → `unspecified-high`, T17 → `deep`
- **Wave 4**: **5 tasks** — T18 → `unspecified-high`, T19 → `unspecified-high`, T20 → `quick`, T21 → `quick`, T22 → `quick`
- **FINAL**: **4 tasks** — F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

- [ ] 1. Add `on_event` Streaming Callback to PipelineContext

  **What to do**:
  - RED: Write test in `tests/agentkit/runtime/test_pipeline_streaming.py` that creates a PipelineContext with an `on_event` async callback, runs Pipeline, and asserts the callback is never called when no events occur (baseline)
  - GREEN: Add `on_event: Callable[[Any], Awaitable[None]] | None = None` field to PipelineContext (or Pipeline config)
  - REFACTOR: Ensure typing is clean, field has proper default (None = no-op)
  - The callback signature: `async def on_event(event: TextEvent | ToolCallEvent | DoneEvent) -> None`
  - This is the general-purpose streaming hook — agentkit knows about agentkit event types only, NOT WireMessages

  **Must NOT do**:
  - Do NOT import anything from `coding_agent.wire` in agentkit
  - Do NOT add WireMessage types to agentkit
  - Do NOT make this a complex event bus — just a simple callback

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Single field addition + 1 test file, very contained change
  - **Skills**: [`test-driven-development`]
    - `test-driven-development`: Enforces RED-GREEN-REFACTOR workflow

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3, 4, 5, 6)
  - **Blocks**: T7, T8, T10, T11
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `src/agentkit/runtime/pipeline.py:1-50` — PipelineContext dataclass definition, see existing fields to add `on_event` alongside them
  - `src/agentkit/runtime/pipeline.py:147-180` — `_stage_run_model` where events are consumed but currently not forwarded

  **API/Type References**:
  - `src/agentkit/providers/models.py` — `TextEvent`, `ToolCallEvent`, `DoneEvent` class definitions — these are the event types the callback will receive

  **Test References**:
  - `tests/agentkit/runtime/test_pipeline.py` — Existing Pipeline tests showing mock patterns and PipelineContext construction
  - `tests/agentkit/runtime/test_pipeline.py` — Use `unittest.mock.AsyncMock` for provider and callback mocking

  **WHY Each Reference Matters**:
  - `pipeline.py:1-50` — Shows where to add the field and what naming convention to follow
  - `models.py` — The exact event types the callback must accept (union type)
  - `test_pipeline.py` — How to construct PipelineContext in tests, what mocks to use

  **Acceptance Criteria**:
  - [ ] `on_event` field exists on PipelineContext with type `Callable | None`, default `None`
  - [ ] `uv run pytest tests/agentkit/runtime/test_pipeline_streaming.py -v` → PASS
  - [ ] `uv run pytest tests/agentkit/ -v` → all 231+ tests still pass (no regression)

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: PipelineContext accepts on_event callback
    Tool: Bash (uv run python -c)
    Preconditions: agentkit package importable
    Steps:
      1. Run: uv run python -c "from agentkit.runtime.pipeline import PipelineContext; print(hasattr(PipelineContext, '__dataclass_fields__') or True)"
      2. Run: uv run python -c "from agentkit.runtime.pipeline import PipelineContext; ctx = PipelineContext.__new__(PipelineContext); print('on_event' in dir(ctx) or hasattr(type(ctx), 'on_event'))"
    Expected Result: Both print True (or field exists)
    Failure Indicators: ImportError, AttributeError, or False output
    Evidence: .sisyphus/evidence/task-1-on-event-field.txt

  Scenario: on_event defaults to None (no callback required)
    Tool: Bash (uv run pytest)
    Preconditions: Test file exists
    Steps:
      1. Run: uv run pytest tests/agentkit/runtime/test_pipeline_streaming.py -v -k "test_on_event_default_none"
    Expected Result: Test passes — PipelineContext can be created without on_event argument
    Failure Indicators: Test failure or TypeError on construction
    Evidence: .sisyphus/evidence/task-1-default-none.txt
  ```

  **Commit**: YES
  - Message: `feat(agentkit): add on_event streaming callback to PipelineContext`
  - Files: `src/agentkit/runtime/pipeline.py`, `tests/agentkit/runtime/test_pipeline_streaming.py`
  - Pre-commit: `uv run pytest tests/agentkit/ -q`

- [ ] 2. Provider Event Adapter (StreamEvent → agentkit events)

  **What to do**:
  - RED: Write test in `tests/coding_agent/plugins/test_llm_provider_adapter.py` asserting that old `StreamEvent(type="delta", content="hello")` gets converted to `TextEvent(text="hello")`
  - GREEN: Add adapter function/method in `src/coding_agent/plugins/llm_provider.py` that wraps old provider's `stream()` output and yields agentkit event types
  - REFACTOR: Ensure all StreamEvent types are mapped: `delta→TextEvent`, `tool_call→ToolCallEvent`, `done→DoneEvent`, `error→DoneEvent(error=...)`
  - This adapter lives in `coding_agent/` (NOT agentkit) — it bridges old providers to new interface

  **Must NOT do**:
  - Do NOT modify old provider code in `src/coding_agent/providers/`
  - Do NOT add old StreamEvent types to agentkit
  - Do NOT rewrite providers — just wrap their output

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Single adapter function, clear input/output mapping
  - **Skills**: [`test-driven-development`]
    - `test-driven-development`: RED-GREEN-REFACTOR for adapter function

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3, 4, 5, 6)
  - **Blocks**: T10
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `src/coding_agent/plugins/llm_provider.py` — Current LLMProviderPlugin implementation where adapter will be added

  **API/Type References**:
  - `src/coding_agent/providers/base.py` — `StreamEvent` class definition with `type` field ("delta", "tool_call", "done", "error")
  - `src/agentkit/providers/models.py` — `TextEvent`, `ToolCallEvent`, `DoneEvent` target types

  **Test References**:
  - `tests/coding_agent/plugins/` — Existing plugin test patterns

  **WHY Each Reference Matters**:
  - `llm_provider.py` — Where the adapter lives, need to understand current `stream_chat` hook
  - `base.py` — Source event format to convert FROM
  - `models.py` — Target event format to convert TO

  **Acceptance Criteria**:
  - [ ] Adapter function converts all 4 StreamEvent types correctly
  - [ ] `uv run pytest tests/coding_agent/plugins/test_llm_provider_adapter.py -v` → PASS
  - [ ] Existing agentkit tests unchanged

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: StreamEvent delta converts to TextEvent
    Tool: Bash (uv run pytest)
    Preconditions: Adapter function exists
    Steps:
      1. Run: uv run pytest tests/coding_agent/plugins/test_llm_provider_adapter.py -v -k "test_delta_to_text_event"
    Expected Result: Test passes — StreamEvent(type="delta", content="hello") → TextEvent(text="hello")
    Failure Indicators: AssertionError on event type or content mismatch
    Evidence: .sisyphus/evidence/task-2-delta-conversion.txt

  Scenario: StreamEvent tool_call converts to ToolCallEvent
    Tool: Bash (uv run pytest)
    Preconditions: Adapter function exists
    Steps:
      1. Run: uv run pytest tests/coding_agent/plugins/test_llm_provider_adapter.py -v -k "test_tool_call_conversion"
    Expected Result: Test passes — tool call fields preserved in conversion
    Failure Indicators: Missing tool name/args in converted event
    Evidence: .sisyphus/evidence/task-2-tool-call-conversion.txt
  ```

  **Commit**: YES
  - Message: `feat(coding_agent): add provider event adapter StreamEvent→agentkit events`
  - Files: `src/coding_agent/plugins/llm_provider.py`, `tests/coding_agent/plugins/test_llm_provider_adapter.py`
  - Pre-commit: `uv run pytest tests/coding_agent/plugins/ -q`

- [ ] 3. Add Tool Execution Error Handling to Pipeline

  **What to do**:
  - RED: Write test in `tests/agentkit/runtime/test_pipeline_errors.py` — mock tool that raises RuntimeError, assert Pipeline catches it and records error in tape (not crash)
  - GREEN: Wrap `call_first("execute_tool", ...)` in `_stage_run_model` with try/except, record error as tape entry
  - REFACTOR: Ensure error entry format is consistent (kind="tool_error", payload={tool_name, error_message})
  - Also emit error event via `on_event` if callback is set

  **Must NOT do**:
  - Do NOT silently swallow errors — they must be recorded in tape
  - Do NOT change the tool execution hook interface
  - Do NOT add retry logic (separate concern)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Small try/except addition + error tape entry, 1-2 files
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 4, 5, 6)
  - **Blocks**: T9
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `src/agentkit/runtime/pipeline.py:180-220` — `_stage_run_model` tool execution loop where try/except needs to be added
  - `src/coding_agent/core/loop.py:220-260` — Old AgentLoop's error handling pattern for reference

  **API/Type References**:
  - `src/agentkit/tape/tape.py` — Tape `append()` method and Entry model for recording errors
  - `src/agentkit/runtime/pipeline.py` — `PipelineError` class for fatal vs recoverable distinction

  **Test References**:
  - `tests/agentkit/runtime/test_pipeline.py` — Existing Pipeline test patterns with mock tools

  **WHY Each Reference Matters**:
  - `pipeline.py:180-220` — Exact location where error handling is missing
  - `loop.py:220-260` — Pattern to follow for error recording (what fields, what format)
  - `tape.py` — How to create an error Entry correctly

  **Acceptance Criteria**:
  - [ ] Tool RuntimeError is caught and recorded in tape (not propagated as crash)
  - [ ] `uv run pytest tests/agentkit/runtime/test_pipeline_errors.py -v` → PASS
  - [ ] `uv run pytest tests/agentkit/ -q` → all tests pass (no regression)

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Tool error is caught and recorded in tape
    Tool: Bash (uv run pytest)
    Preconditions: Error handling test exists
    Steps:
      1. Run: uv run pytest tests/agentkit/runtime/test_pipeline_errors.py -v -k "test_tool_error_recorded_in_tape"
    Expected Result: Test passes — tape contains error entry with tool name and error message
    Failure Indicators: Pipeline crashes with unhandled RuntimeError
    Evidence: .sisyphus/evidence/task-3-tool-error-handling.txt

  Scenario: Pipeline continues after tool error (doesn't crash)
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/agentkit/runtime/test_pipeline_errors.py -v -k "test_pipeline_continues_after_error"
    Expected Result: Test passes — Pipeline run_turn completes, returns context with error in tape
    Failure Indicators: PipelineError raised or test timeout
    Evidence: .sisyphus/evidence/task-3-pipeline-continues.txt
  ```

  **Commit**: YES
  - Message: `feat(agentkit): add error handling in Pipeline tool execution`
  - Files: `src/agentkit/runtime/pipeline.py`, `tests/agentkit/runtime/test_pipeline_errors.py`
  - Pre-commit: `uv run pytest tests/agentkit/ -q`

- [ ] 4. Add Tool Result Truncation via Hook

  **What to do**:
  - RED: Write test in `tests/agentkit/runtime/test_pipeline_truncation.py` — tool returns 1MB string, assert result is truncated to configured max size
  - GREEN: Add `truncate_tool_result` hook point in Pipeline (or post-processing in execute_tool path), with configurable `max_tool_result_size` (default 50KB to match old `MAX_TOOL_RESULT_SIZE`)
  - REFACTOR: Ensure truncation adds a `[truncated, showing first N chars of M total]` suffix

  **Must NOT do**:
  - Do NOT hardcode the truncation size — make it configurable
  - Do NOT truncate in agentkit tools directly — do it at Pipeline level

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple post-processing hook, 1-2 files
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3, 5, 6)
  - **Blocks**: T9
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `src/coding_agent/core/loop.py` — Search for `MAX_TOOL_RESULT_SIZE` or `MAX_RESULT_SIZE` to find old truncation logic and size constant

  **API/Type References**:
  - `src/agentkit/runtime/pipeline.py:180-220` — Tool result handling in `_stage_run_model` where truncation should be applied
  - `src/agentkit/runtime/hook_runtime.py` — Hook system for adding new hook points

  **WHY Each Reference Matters**:
  - `loop.py` MAX_RESULT_SIZE — The exact truncation size and format to replicate
  - `pipeline.py` tool result path — Where to insert truncation logic
  - `hook_runtime.py` — If implementing as a hook rather than inline

  **Acceptance Criteria**:
  - [ ] Tool results exceeding max size are truncated with informative suffix
  - [ ] Default max size matches old stack's `MAX_TOOL_RESULT_SIZE`
  - [ ] `uv run pytest tests/agentkit/runtime/test_pipeline_truncation.py -v` → PASS
  - [ ] `uv run pytest tests/agentkit/ -q` → all tests pass

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Large tool result is truncated
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/agentkit/runtime/test_pipeline_truncation.py -v -k "test_large_result_truncated"
    Expected Result: 1MB tool result truncated to ~50KB with "[truncated...]" suffix
    Failure Indicators: Full 1MB result in tape, or no truncation message
    Evidence: .sisyphus/evidence/task-4-truncation.txt

  Scenario: Small tool results are NOT truncated
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/agentkit/runtime/test_pipeline_truncation.py -v -k "test_small_result_not_truncated"
    Expected Result: Result under max size passes through unchanged
    Failure Indicators: Small result modified or truncation suffix added
    Evidence: .sisyphus/evidence/task-4-no-truncation.txt
  ```

  **Commit**: YES
  - Message: `feat(agentkit): add tool result truncation via hook`
  - Files: `src/agentkit/runtime/pipeline.py`, `tests/agentkit/runtime/test_pipeline_truncation.py`
  - Pre-commit: `uv run pytest tests/agentkit/ -q`

- [ ] 5. TurnOutcome Dataclass for Pipeline Results

  **What to do**:
  - RED: Write test in `tests/coding_agent/test_adapter_types.py` asserting TurnOutcome has fields: `stop_reason: str`, `final_message: str | None`, `steps_taken: int`, `error: str | None`
  - GREEN: Create `src/coding_agent/adapter_types.py` with `TurnOutcome` dataclass matching old AgentLoop's return contract
  - REFACTOR: Add `StopReason` enum: `no_tool_calls`, `max_steps_reached`, `doom_loop`, `error`
  - This dataclass is the bridge between Pipeline (returns PipelineContext) and CLI (expects structured outcome)

  **Must NOT do**:
  - Do NOT put TurnOutcome in agentkit — it's a coding_agent concept
  - Do NOT add fields beyond what old AgentLoop's TurnOutcome had

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Single dataclass + enum definition, trivial
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3, 4, 6)
  - **Blocks**: T10, T12
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `src/coding_agent/core/loop.py` — Search for `TurnOutcome` or the return value of `run_turn()` to find old result shape

  **API/Type References**:
  - `src/coding_agent/cli/repl.py:190` — Where `result.stop_reason` and `result.steps_taken` are read
  - `src/coding_agent/__main__.py:285` — Where `result.stop_reason` is checked for batch mode exit

  **WHY Each Reference Matters**:
  - `loop.py` TurnOutcome — The exact shape to replicate (fields, types, defaults)
  - `repl.py:190` and `__main__.py:285` — The consumers of TurnOutcome, showing which fields are actually used

  **Acceptance Criteria**:
  - [ ] `TurnOutcome` dataclass with stop_reason, final_message, steps_taken, error
  - [ ] `StopReason` enum with 4 values matching old stack
  - [ ] `uv run pytest tests/coding_agent/test_adapter_types.py -v` → PASS

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: TurnOutcome is importable and constructable
    Tool: Bash (uv run python -c)
    Steps:
      1. Run: uv run python -c "from coding_agent.adapter_types import TurnOutcome, StopReason; t = TurnOutcome(stop_reason=StopReason.NO_TOOL_CALLS, final_message='done', steps_taken=3, error=None); print(t.stop_reason, t.steps_taken)"
    Expected Result: Prints "StopReason.NO_TOOL_CALLS 3" (or similar enum repr)
    Failure Indicators: ImportError or TypeError
    Evidence: .sisyphus/evidence/task-5-turn-outcome.txt

  Scenario: StopReason enum has all required values
    Tool: Bash (uv run python -c)
    Steps:
      1. Run: uv run python -c "from coding_agent.adapter_types import StopReason; print([e.value for e in StopReason])"
    Expected Result: List includes no_tool_calls, max_steps_reached, doom_loop, error
    Failure Indicators: Missing enum members
    Evidence: .sisyphus/evidence/task-5-stop-reasons.txt
  ```

  **Commit**: YES
  - Message: `feat(coding_agent): add TurnOutcome dataclass for Pipeline results`
  - Files: `src/coding_agent/adapter_types.py`, `tests/coding_agent/test_adapter_types.py`
  - Pre-commit: `uv run pytest tests/coding_agent/ -q`

- [ ] 6. Feature Toggle Infrastructure (USE_PIPELINE env var)

  **What to do**:
  - RED: Write test in `tests/coding_agent/test_feature_toggle.py` asserting `use_pipeline()` returns True when `USE_PIPELINE=1` env var is set, False otherwise
  - GREEN: Add `use_pipeline()` function in `src/coding_agent/core/config.py` (alongside existing `load_config()` and `settings`) that reads `USE_PIPELINE` env var
  - REFACTOR: Add `--use-pipeline` CLI flag as alternative to env var, defaulting to False initially (old path)
  - During transition, both paths coexist. After Wave 3 verification, toggle flips to True by default (T20).

  **Must NOT do**:
  - Do NOT make Pipeline the default yet — toggle defaults to False (old path)
  - Do NOT add complex feature flag framework — just env var + CLI flag

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Env var check + CLI flag, trivial
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3, 4, 5)
  - **Blocks**: T13, T14, T15
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `src/coding_agent/__main__.py:1-50` — CLI argument parsing (click commands), where --use-pipeline flag should be added
  - `src/coding_agent/__main__.py:219-286` — `_run_with_tui()` where toggle will branch between old AgentLoop and new PipelineAdapter
  - `src/coding_agent/__main__.py:288-354` — `_run_headless()` where toggle will branch
  - `src/coding_agent/core/config.py:59-79` — Existing `load_config()` and `settings` — add `use_pipeline()` alongside

  **WHY Each Reference Matters**:
  - `__main__.py` CLI args — Where to add the flag and where to read it for branching

  **Acceptance Criteria**:
  - [ ] `USE_PIPELINE=1` env var → `use_pipeline()` returns True
  - [ ] No env var → `use_pipeline()` returns False
  - [ ] `--use-pipeline` CLI flag works
  - [ ] `uv run pytest tests/coding_agent/test_feature_toggle.py -v` → PASS

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Toggle reads env var correctly
    Tool: Bash
    Steps:
      1. Run: USE_PIPELINE=1 uv run python -c "from coding_agent.core.config import use_pipeline; print(use_pipeline())"
      2. Run: uv run python -c "from coding_agent.core.config import use_pipeline; print(use_pipeline())"
    Expected Result: First prints True, second prints False
    Failure Indicators: Both print same value, or ImportError
    Evidence: .sisyphus/evidence/task-6-toggle.txt

  Scenario: Toggle defaults to old path (False)
    Tool: Bash
    Steps:
      1. Run: uv run python -c "from coding_agent.core.config import use_pipeline; assert not use_pipeline(), 'Should default to False'"
    Expected Result: No assertion error
    Failure Indicators: AssertionError
    Evidence: .sisyphus/evidence/task-6-default-false.txt
  ```

  **Commit**: YES
  - Message: `feat(coding_agent): add USE_PIPELINE feature toggle`
  - Files: `src/coding_agent/core/config.py`, `tests/coding_agent/test_feature_toggle.py`
  - Pre-commit: `uv run pytest tests/coding_agent/ -q`

- [ ] 6b. Add `file_patch` and `subagent` Tools to CoreToolsPlugin (CLI Tool Parity)

  **What to do**:
  - RED: Write test in `tests/coding_agent/plugins/test_core_tools_parity.py` asserting CoreToolsPlugin registers `file_patch` and `subagent` tools alongside existing tools
  - GREEN: In `src/coding_agent/plugins/core_tools.py`, register `file_patch` (porting from `src/coding_agent/tools/file.py:file_patch`) and `subagent` (porting from `src/coding_agent/tools/subagent.py`)
  - REFACTOR: Ensure `file_patch` and `subagent` follow the same `@tool` decorator pattern as existing CoreToolsPlugin tools
  - **Context**: The current CoreToolsPlugin only registers: file_read, file_write, file_replace, glob_files, grep_search, bash_run, todo_write, todo_read. The old CLI stack also uses `file_patch` and `subagent`. Without these, the Pipeline path would have fewer capabilities than the old AgentLoop path, violating guardrail G3 (no old code deletion until feature parity) and G6 (keep old subagent working).

  **Must NOT do**:
  - Do NOT rewrite `file_patch` logic — port the existing algorithm from `src/coding_agent/tools/file.py`
  - Do NOT redesign `subagent` — create a thin wrapper that calls the existing subagent spawn logic
  - Do NOT add these tools to agentkit's base tools — they belong in `coding_agent/plugins/core_tools.py`

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Two tool registrations requiring understanding of existing implementations + plugin integration
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3, 4, 5, 6)
  - **Blocks**: T10, T13, T14, T15 (all paths that use create_agent() need full tool set)
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `src/coding_agent/plugins/core_tools.py:1-63` — Current CoreToolsPlugin with 8 registered tools — add `file_patch` and `subagent` following same pattern
  - `src/coding_agent/tools/file.py` — Search for `file_patch` function — the implementation to port (diff-based file editing)
  - `src/coding_agent/tools/subagent.py:1-116` — Full subagent tool: spawns child agent with separate tape/context, returns result

  **API/Type References**:
  - `src/agentkit/tools/decorator.py` — `@tool` decorator for registering new tools
  - `src/coding_agent/__main__.py:11-107` — `create_agent()` where CoreToolsPlugin is mounted — verify new tools appear after registration

  **Test References**:
  - `tests/tools/test_file.py` — Existing file_patch tests (for reference on behavior expectations)
  - `tests/tools/test_subagent.py` — Existing subagent tests (if any)

  **WHY Each Reference Matters**:
  - `core_tools.py` — The file being modified, need to follow its pattern exactly
  - `tools/file.py` file_patch — The algorithm to replicate (diff application, validation, error handling)
  - `tools/subagent.py` — The subagent spawn logic (depth limits, separate context, result extraction)
  - `decorator.py` — Correct tool registration API

  **Acceptance Criteria**:
  - [ ] CoreToolsPlugin registers `file_patch` tool
  - [ ] CoreToolsPlugin registers `subagent` tool
  - [ ] `create_agent()` pipeline has all 10 tools (8 original + file_patch + subagent)
  - [ ] `uv run pytest tests/coding_agent/plugins/test_core_tools_parity.py -v` → PASS
  - [ ] Existing CoreToolsPlugin tests still pass

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: CoreToolsPlugin registers file_patch tool
    Tool: Bash (uv run python -c)
    Steps:
      1. Run: uv run python -c "from coding_agent.plugins.core_tools import CoreToolsPlugin; p = CoreToolsPlugin(); names = [t.name for t in p.tools()]; assert 'file_patch' in names, f'Missing file_patch, got: {names}'"
    Expected Result: Assertion passes — file_patch is registered
    Failure Indicators: AssertionError listing tools without file_patch
    Evidence: .sisyphus/evidence/task-6b-file-patch.txt

  Scenario: CoreToolsPlugin registers subagent tool
    Tool: Bash (uv run python -c)
    Steps:
      1. Run: uv run python -c "from coding_agent.plugins.core_tools import CoreToolsPlugin; p = CoreToolsPlugin(); names = [t.name for t in p.tools()]; assert 'subagent' in names, f'Missing subagent, got: {names}'"
    Expected Result: Assertion passes — subagent is registered
    Failure Indicators: AssertionError listing tools without subagent
    Evidence: .sisyphus/evidence/task-6b-subagent.txt

  Scenario: create_agent() pipeline includes all 10 tools
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/coding_agent/plugins/test_core_tools_parity.py -v -k "test_full_tool_count"
    Expected Result: Pipeline has 10 registered tools
    Failure Indicators: Tool count < 10
    Evidence: .sisyphus/evidence/task-6b-full-count.txt
  ```

  **Commit**: YES
  - Message: `feat(coding_agent): add file_patch and subagent tools to CoreToolsPlugin`
  - Files: `src/coding_agent/plugins/core_tools.py`, `tests/coding_agent/plugins/test_core_tools_parity.py`
  - Pre-commit: `uv run pytest tests/coding_agent/ -q`

- [ ] 7. Pipeline Emits Streaming Events During run_model Stage

  **What to do**:
  - RED: Write test in `tests/agentkit/runtime/test_pipeline_streaming.py` — mock provider yields TextEvent("hello"), assert `on_event` callback receives it during `run_turn()`
  - GREEN: In `_stage_run_model`, after each provider event (TextEvent, ToolCallEvent, DoneEvent), call `await ctx.on_event(event)` if callback is set
  - RED: Write test for ToolCallEvent streaming — provider yields ToolCallEvent, assert callback receives it
  - GREEN: Emit ToolCallEvent through callback
  - REFACTOR: Add DoneEvent emission at end of model call
  - This is the core streaming implementation that makes real-time TUI output possible

  **Must NOT do**:
  - Do NOT buffer events — emit immediately as received from provider
  - Do NOT filter/transform events — pass through as-is
  - Do NOT add WireMessage conversion here — that's PipelineAdapter's job (T10)

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Core streaming mechanism, needs careful async handling, multiple test cases
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 8, 9, 10, 11, 12)
  - **Blocks**: T10
  - **Blocked By**: T1

  **References**:

  **Pattern References**:
  - `src/agentkit/runtime/pipeline.py:147-180` — `_stage_run_model` where provider events are consumed — THIS is where `on_event` calls must be inserted
  - `src/agentkit/runtime/pipeline.py:161-176` — The `async for event in provider_stream` loop where each event is processed

  **API/Type References**:
  - `src/agentkit/providers/models.py` — `TextEvent`, `ToolCallEvent`, `DoneEvent` — the events to emit
  - Task 1's output — The `on_event` callback field on PipelineContext

  **Test References**:
  - `tests/agentkit/runtime/test_pipeline.py` — Mock provider patterns

  **WHY Each Reference Matters**:
  - `pipeline.py:161-176` — The exact loop where each event must be forwarded to callback
  - `models.py` — Event types to test against
  - `test_pipeline.py` — How to mock the provider stream

  **Acceptance Criteria**:
  - [ ] TextEvent from provider → on_event callback called with TextEvent
  - [ ] ToolCallEvent from provider → on_event callback called with ToolCallEvent
  - [ ] DoneEvent emitted at end of model call
  - [ ] No on_event set → no error (silently skipped)
  - [ ] `uv run pytest tests/agentkit/runtime/test_pipeline_streaming.py -v` → PASS (all streaming tests)
  - [ ] `uv run pytest tests/agentkit/ -q` → no regression

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: TextEvent streams through on_event callback
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/agentkit/runtime/test_pipeline_streaming.py -v -k "test_text_event_streamed"
    Expected Result: Mock on_event receives TextEvent with correct text content
    Failure Indicators: Callback never called, or wrong event type received
    Evidence: .sisyphus/evidence/task-7-text-streaming.txt

  Scenario: Multiple events stream in order
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/agentkit/runtime/test_pipeline_streaming.py -v -k "test_events_stream_in_order"
    Expected Result: on_event receives TextEvent, ToolCallEvent, DoneEvent in provider order
    Failure Indicators: Events out of order or missing
    Evidence: .sisyphus/evidence/task-7-event-order.txt
  ```

  **Commit**: YES
  - Message: `feat(agentkit): emit streaming events during run_model stage`
  - Files: `src/agentkit/runtime/pipeline.py`, `tests/agentkit/runtime/test_pipeline_streaming.py`
  - Pre-commit: `uv run pytest tests/agentkit/ -q`

- [ ] 8. DoomDetectorPlugin

  **What to do**:
  - RED: Write test in `tests/coding_agent/plugins/test_doom_detector.py` — simulate 4 identical tool calls in tape, assert plugin stops the turn with doom_loop reason
  - GREEN: Create `src/coding_agent/plugins/doom_detector.py` — plugin that hooks into `pre_tool_call` or `on_checkpoint` to detect repetitive patterns
  - RED: Write test for threshold configurability (default 3)
  - GREEN: Add `threshold` config parameter
  - REFACTOR: Extract hash-based detection logic from old `src/coding_agent/core/doom.py`
  - The plugin registers hooks: `on_checkpoint` to check tape for repeated patterns after each tool round

  **Must NOT do**:
  - Do NOT copy-paste old DoomDetector — rewrite as clean plugin using hook system
  - Do NOT import from `coding_agent.core.doom` — new implementation only
  - Do NOT block on first match — use the same threshold logic as old (3 consecutive identical)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Algorithm logic (hash-based repetition detection) + plugin integration
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7, 9, 10, 11, 12)
  - **Blocks**: T17
  - **Blocked By**: T1

  **References**:

  **Pattern References**:
  - `src/coding_agent/core/doom.py` — Old DoomDetector implementation — understand the hash-based algorithm and threshold logic, then reimplement as plugin
  - `src/coding_agent/plugins/summarizer.py` — Example of a plugin that hooks into pipeline lifecycle — follow this pattern

  **API/Type References**:
  - `src/agentkit/runtime/hook_runtime.py` — Hook registration API for plugins
  - `src/agentkit/tape/tape.py` — Tape.entries access for reading recent tool calls

  **Test References**:
  - `tests/coding_agent/plugins/` — Existing plugin test patterns

  **WHY Each Reference Matters**:
  - `doom.py` — Algorithm to replicate: how it hashes tool calls, what threshold means
  - `summarizer.py` — How to structure a coding_agent plugin (mount, hook registration)
  - `hook_runtime.py` — Which hooks are available for doom detection

  **Acceptance Criteria**:
  - [ ] Plugin detects 3+ identical consecutive tool call patterns
  - [ ] Plugin signals doom_loop stop reason (via context or exception)
  - [ ] Threshold is configurable
  - [ ] Non-consecutive identical calls do NOT trigger (only consecutive)
  - [ ] `uv run pytest tests/coding_agent/plugins/test_doom_detector.py -v` → PASS

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Doom loop detected after 3 identical tool calls
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/coding_agent/plugins/test_doom_detector.py -v -k "test_doom_loop_detected"
    Expected Result: Plugin raises or signals doom_loop after 3rd identical tool call
    Failure Indicators: Pipeline continues past 3rd identical call
    Evidence: .sisyphus/evidence/task-8-doom-detected.txt

  Scenario: Different tool calls don't trigger doom
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/coding_agent/plugins/test_doom_detector.py -v -k "test_different_calls_no_doom"
    Expected Result: 5 different tool calls proceed without doom detection
    Failure Indicators: False positive doom detection
    Evidence: .sisyphus/evidence/task-8-no-false-positive.txt
  ```

  **Commit**: YES
  - Message: `feat(coding_agent): add DoomDetectorPlugin`
  - Files: `src/coding_agent/plugins/doom_detector.py`, `tests/coding_agent/plugins/test_doom_detector.py`
  - Pre-commit: `uv run pytest tests/coding_agent/ -q`

- [ ] 9. ParallelExecutorPlugin

  **What to do**:
  - RED: Write test in `tests/coding_agent/plugins/test_parallel_executor.py` — 3 independent file_read tool calls execute concurrently (wall time < 3× single)
  - GREEN: Create `src/coding_agent/plugins/parallel_executor.py` — plugin that hooks into `execute_tool` (or provides batch execution) using asyncio.gather for independent tools
  - RED: Write test for dependency detection — file_read + file_write on same file → sequential
  - GREEN: Add dependency analysis (same file = dependent, different files = independent)
  - REFACTOR: Make max parallelism configurable (default 5)
  - Port the dependency detection logic from old `src/coding_agent/core/parallel.py`

  **Must NOT do**:
  - Do NOT parallelize all tools blindly — must respect dependencies
  - Do NOT modify agentkit's core tool execution — add as plugin/hook override
  - Do NOT add complex DAG scheduling — simple independent/dependent classification is enough

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Async concurrency logic + dependency analysis + timing-sensitive tests
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7, 8, 10, 11, 12)
  - **Blocks**: T17
  - **Blocked By**: T3, T4

  **References**:

  **Pattern References**:
  - `src/coding_agent/core/parallel.py` — Old ParallelExecutor: dependency analysis algorithm, safety classifications, asyncio.gather usage
  - `src/coding_agent/plugins/core_tools.py` — How tools are registered — need to understand tool names for dependency rules

  **API/Type References**:
  - `src/agentkit/runtime/hook_runtime.py` — Hook system — `execute_tool` hook that this plugin will override/augment
  - `src/agentkit/runtime/pipeline.py:180-220` — How Pipeline currently calls execute_tool sequentially

  **Test References**:
  - `tests/core/test_parallel.py` (if exists) — Old parallel execution tests for reference

  **WHY Each Reference Matters**:
  - `parallel.py` — Core algorithm: which tools can be parallel, how dependencies are detected
  - `core_tools.py` — Tool names used in dependency rules (file_read, file_write, bash_run etc)
  - `hook_runtime.py` — How to override execute_tool behavior

  **Acceptance Criteria**:
  - [ ] Independent tools execute concurrently via asyncio.gather
  - [ ] Dependent tools (same file read+write) execute sequentially
  - [ ] Max parallelism is configurable (default 5)
  - [ ] Wall time for 3 independent slow tools < 2× single execution time
  - [ ] `uv run pytest tests/coding_agent/plugins/test_parallel_executor.py -v` → PASS

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Independent tools run in parallel
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/coding_agent/plugins/test_parallel_executor.py -v -k "test_independent_tools_parallel"
    Expected Result: 3 tools with 100ms delay each complete in <250ms total (not 300ms+)
    Failure Indicators: Wall time ≥300ms indicating sequential execution
    Evidence: .sisyphus/evidence/task-9-parallel-execution.txt

  Scenario: Dependent tools run sequentially
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/coding_agent/plugins/test_parallel_executor.py -v -k "test_dependent_tools_sequential"
    Expected Result: file_read(a) + file_write(a) execute in order, not parallel
    Failure Indicators: Race condition or write before read
    Evidence: .sisyphus/evidence/task-9-sequential-deps.txt
  ```

  **Commit**: YES
  - Message: `feat(agentkit): add parallel tool execution support`
  - Files: `src/coding_agent/plugins/parallel_executor.py`, `tests/coding_agent/plugins/test_parallel_executor.py`
  - Pre-commit: `uv run pytest tests/coding_agent/ -q`

- [ ] 10. PipelineAdapter: Pipeline → TurnOutcome + WireMessages

  **What to do**:
  - This is the **core bridge** between agentkit Pipeline and the CLI/TUI layer
  - RED: Write test in `tests/coding_agent/test_pipeline_adapter.py` — PipelineAdapter.run_turn("hello") returns TurnOutcome with correct stop_reason
  - GREEN: Create `src/coding_agent/adapter.py` with `PipelineAdapter` class:
    - Constructor: receives Pipeline + PipelineContext (from `create_agent()`)
    - `async def run_turn(self, user_input: str) -> TurnOutcome`:
      1. Append user message to PipelineContext tape
      2. Set up `on_event` callback that converts agentkit events → WireMessages and forwards to WireConsumer
      3. Call `pipeline.run_turn(ctx)`
      4. Extract stop_reason from context (no_tool_calls, max_steps, doom, error)
      5. Build and return TurnOutcome
  - RED: Write test that on_event callback converts TextEvent → StreamDelta WireMessage
  - GREEN: Implement event → WireMessage conversion in the callback
  - REFACTOR: Add proper WireMessage emission for ToolCallBegin/End, TurnBegin/End

  **Must NOT do**:
  - Do NOT put WireMessage conversion logic in agentkit — it stays in coding_agent/adapter.py
  - Do NOT modify Pipeline itself — adapter wraps it
  - Do NOT add new WireMessage types — use existing ones from wire/protocol.py

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Core integration piece bridging two architectures, needs careful async handling and correct event mapping
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (once dependencies met)
  - **Parallel Group**: Wave 2 (with Tasks 7, 8, 9, 11, 12)
  - **Blocks**: T12, T13, T14, T15
  - **Blocked By**: T1, T2, T5, T6b, T7

  **References**:

  **Pattern References**:
  - `src/coding_agent/core/loop.py:183-289` — Old AgentLoop.run_turn() — the EXACT behavior to replicate: how it builds TurnOutcome, emits WireMessages, handles stop conditions
  - `src/coding_agent/ui/rich_consumer.py:40-59` — RichConsumer.emit() — the 3 WireMessage types actually consumed (TurnEnd, StreamDelta, ToolCallDelta)
  - `src/coding_agent/__main__.py:11-107` — `create_agent()` function that builds Pipeline+PipelineContext — adapter receives these. **NOTE**: `create_agent()` currently only registers CoreToolsPlugin tools (file_read, file_write, file_replace, glob_files, grep_search, bash_run, todo_write, todo_read). It does NOT register `file_patch` or `subagent` tools. Task 6b adds those to CoreToolsPlugin before this task.

  **API/Type References**:
  - `src/coding_agent/wire/protocol.py` — WireMessage types: StreamDelta, ToolCallDelta, TurnEnd (the 3 that matter)
  - `src/agentkit/providers/models.py` — TextEvent, ToolCallEvent, DoneEvent — input events to convert FROM
  - Task 5's TurnOutcome — output type to produce
  - Task 2's event adapter — provider event conversion (may be reused)

  **Test References**:
  - `tests/integration/test_e2e.py` — End-to-end patterns showing how Pipeline is tested

  **WHY Each Reference Matters**:
  - `loop.py:183-289` — The exact TurnOutcome construction and stop_reason logic to replicate
  - `rich_consumer.py:40-59` — Only 3 WireMessage types matter (optimization: don't emit unused types)
  - `create_agent()` — The factory that provides Pipeline+PipelineContext to adapter
  - `protocol.py` — WireMessage class definitions for the bridge

  **Acceptance Criteria**:
  - [ ] `PipelineAdapter.run_turn("hello")` → returns TurnOutcome
  - [ ] TextEvent → StreamDelta WireMessage emitted to consumer
  - [ ] ToolCallEvent → ToolCallDelta WireMessage emitted
  - [ ] DoneEvent → TurnEnd WireMessage emitted
  - [ ] stop_reason correctly maps from Pipeline context state
  - [ ] `uv run pytest tests/coding_agent/test_pipeline_adapter.py -v` → PASS

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: PipelineAdapter returns TurnOutcome from Pipeline execution
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/coding_agent/test_pipeline_adapter.py -v -k "test_run_turn_returns_outcome"
    Expected Result: TurnOutcome with stop_reason=no_tool_calls after simple LLM response
    Failure Indicators: Wrong stop_reason or missing fields
    Evidence: .sisyphus/evidence/task-10-turn-outcome.txt

  Scenario: Streaming events bridge to WireMessages
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/coding_agent/test_pipeline_adapter.py -v -k "test_text_event_to_stream_delta"
    Expected Result: TextEvent("hello") → WireConsumer receives StreamDelta with content "hello"
    Failure Indicators: Consumer.emit() never called, or wrong WireMessage type
    Evidence: .sisyphus/evidence/task-10-wire-bridge.txt
  ```

  **Commit**: YES
  - Message: `feat(coding_agent): add PipelineAdapter bridging Pipeline→CLI`
  - Files: `src/coding_agent/adapter.py`, `tests/coding_agent/test_pipeline_adapter.py`
  - Pre-commit: `uv run pytest tests/coding_agent/ -q`

- [ ] 11. SessionMetricsPlugin

  **What to do**:
  - RED: Write test in `tests/coding_agent/plugins/test_metrics.py` — after run_turn, metrics plugin has recorded API latency and tool execution times
  - GREEN: Create `src/coding_agent/plugins/metrics.py` — plugin that hooks into `on_checkpoint` and timing hooks to collect: API call latency, tool execution duration, total turn time, steps count
  - REFACTOR: Match old SessionMetrics fields from `core/loop.py`

  **Must NOT do**:
  - Do NOT add external metrics dependencies (no prometheus/datadog/etc)
  - Do NOT emit metrics to any service — just collect in-memory for display

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Timing instrumentation across multiple hook points
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7, 8, 9, 10, 12)
  - **Blocks**: T17
  - **Blocked By**: T1

  **References**:

  **Pattern References**:
  - `src/coding_agent/core/loop.py` — Search for `SessionMetrics` or `metrics` to find old metrics collection pattern
  - `src/coding_agent/plugins/summarizer.py` — Plugin pattern for hook registration

  **API/Type References**:
  - `src/agentkit/runtime/hook_runtime.py` — Available hooks for timing instrumentation

  **WHY Each Reference Matters**:
  - `loop.py` SessionMetrics — What metrics to collect (fields, format)
  - `summarizer.py` — How to write a plugin with multiple hooks

  **Acceptance Criteria**:
  - [ ] Plugin records API call latency per model call
  - [ ] Plugin records tool execution duration per tool
  - [ ] Metrics accessible after run_turn via plugin.get_metrics()
  - [ ] `uv run pytest tests/coding_agent/plugins/test_metrics.py -v` → PASS

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Metrics recorded after pipeline turn
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/coding_agent/plugins/test_metrics.py -v -k "test_metrics_recorded"
    Expected Result: After run_turn, metrics contain api_latency > 0 and steps_count > 0
    Failure Indicators: Empty metrics or zero values for timed operations
    Evidence: .sisyphus/evidence/task-11-metrics.txt

  Scenario: Metrics reset between turns
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/coding_agent/plugins/test_metrics.py -v -k "test_metrics_per_turn"
    Expected Result: Each turn has independent metrics, not accumulated
    Failure Indicators: Metrics from turn 1 leak into turn 2
    Evidence: .sisyphus/evidence/task-11-per-turn.txt
  ```

  **Commit**: YES
  - Message: `feat(coding_agent): add SessionMetricsPlugin`
  - Files: `src/coding_agent/plugins/metrics.py`, `tests/coding_agent/plugins/test_metrics.py`
  - Pre-commit: `uv run pytest tests/coding_agent/ -q`

- [ ] 12. REPL-safe Error Recovery in PipelineAdapter

  **What to do**:
  - RED: Write test in `tests/coding_agent/test_pipeline_adapter.py` — Pipeline raises PipelineError during run_turn, adapter catches it and returns TurnOutcome with stop_reason=error (REPL continues)
  - GREEN: In PipelineAdapter.run_turn(), wrap pipeline.run_turn() in try/except PipelineError, build error TurnOutcome
  - RED: Write test for tape preservation — user message is NOT lost on error (Pipeline rolls back tape, but adapter preserves user message)
  - GREEN: Append user message to tape BEFORE calling pipeline.run_turn(). On error, re-append user message if Pipeline rolled it back
  - REFACTOR: Also handle KeyboardInterrupt gracefully — return TurnOutcome with stop_reason=interrupted

  **Must NOT do**:
  - Do NOT swallow errors silently — still record them in TurnOutcome.error
  - Do NOT crash the REPL on any Pipeline error — always return TurnOutcome
  - Do NOT modify Pipeline's rollback behavior — handle it in adapter

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Error recovery logic with tape state management
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (once dependencies met)
  - **Parallel Group**: Wave 2 (with Tasks 7, 8, 9, 10, 11)
  - **Blocks**: T14
  - **Blocked By**: T5, T10

  **References**:

  **Pattern References**:
  - `src/agentkit/runtime/pipeline.py:100-110` — Pipeline's tape rollback on error (restores original_tape)
  - `src/coding_agent/cli/repl.py:140-150` — Old REPL error handling (catches exceptions, lets user continue)

  **API/Type References**:
  - `src/agentkit/runtime/pipeline.py` — `PipelineError` exception class
  - Task 5's TurnOutcome — The error return type
  - Task 10's PipelineAdapter — Where error handling is added

  **WHY Each Reference Matters**:
  - `pipeline.py:100-110` — Understanding rollback behavior to compensate for
  - `repl.py:140-150` — The error UX to preserve (user sees error, can continue)

  **Acceptance Criteria**:
  - [ ] PipelineError → TurnOutcome(stop_reason=error, error=message), REPL continues
  - [ ] User message preserved in tape even after Pipeline error
  - [ ] KeyboardInterrupt → graceful stop, not crash
  - [ ] `uv run pytest tests/coding_agent/test_pipeline_adapter.py -v -k error` → PASS

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Pipeline error returns error TurnOutcome (REPL doesn't crash)
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/coding_agent/test_pipeline_adapter.py -v -k "test_pipeline_error_returns_outcome"
    Expected Result: TurnOutcome with stop_reason=error and error message set
    Failure Indicators: PipelineError propagates unhandled
    Evidence: .sisyphus/evidence/task-12-error-recovery.txt

  Scenario: User message preserved after Pipeline error
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/coding_agent/test_pipeline_adapter.py -v -k "test_user_message_preserved_on_error"
    Expected Result: Tape still contains user message after error recovery
    Failure Indicators: Tape rolled back to state before user message
    Evidence: .sisyphus/evidence/task-12-message-preserved.txt
  ```

  **Commit**: YES (groups with T10)
  - Message: `fix(coding_agent): REPL-safe error recovery in PipelineAdapter`
  - Files: `src/coding_agent/adapter.py`, `tests/coding_agent/test_pipeline_adapter.py`
  - Pre-commit: `uv run pytest tests/coding_agent/ -q`

- [ ] 13. Wire `run` Command to PipelineAdapter

  **What to do**:
  - RED: Write test in `tests/coding_agent/test_cli_pipeline.py` — with USE_PIPELINE=1, `run` command creates PipelineAdapter instead of AgentLoop
  - GREEN: In `src/coding_agent/__main__.py`, add branch in `_run_with_tui()` and `_run_headless()`: if `use_pipeline()` → create PipelineAdapter via `create_agent()`, else → old AgentLoop path
  - REFACTOR: Extract shared setup logic between old and new paths
  - The `run` command is the simpler case (single turn, exit after completion)

  **Must NOT do**:
  - Do NOT remove old AgentLoop path yet — both coexist behind toggle
  - Do NOT change TUI rendering code — PipelineAdapter emits same WireMessages
  - Do NOT skip tool parity check — verify `create_agent()` registers all tools CLI needs (file_patch, subagent added by T6b)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: CLI wiring with branching logic, needs understanding of both stacks
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 14, 15, 16)
  - **Blocks**: T17
  - **Blocked By**: T6, T10

  **References**:

  **Pattern References**:
  - `src/coding_agent/__main__.py:219-286` — `_run_with_tui()` — the function to modify with branching logic
  - `src/coding_agent/__main__.py:288-354` — `_run_headless()` — same branching needed here
  - `src/coding_agent/__main__.py:11-107` — `create_agent()` — factory for Pipeline+PipelineContext

  **API/Type References**:
  - Task 6's `use_pipeline()` — Feature toggle function (in `coding_agent.core.config`)
  - Task 10's `PipelineAdapter` — The adapter to instantiate
  - `src/coding_agent/ui/rich_tui.py` — TUI expects WireConsumer — verify adapter provides one

  **Test References**:
  - `tests/cli/test_commands.py` — Existing CLI command test patterns
  - `tests/coding_agent/test_bootstrap.py` — Tests for create_agent() factory

  **WHY Each Reference Matters**:
  - `__main__.py:219-286` and `288-354` — EXACT locations to add the if/else branches
  - `create_agent()` at lines 11-107 — How to get Pipeline+PipelineContext for the adapter
  - TUI — Verify adapter's WireConsumer output is compatible

  **Acceptance Criteria**:
  - [ ] `USE_PIPELINE=1 uv run python -m coding_agent run --goal "echo test"` → uses PipelineAdapter
  - [ ] Without env var → still uses old AgentLoop (no regression)
  - [ ] TUI renders streaming output identically
  - [ ] `uv run pytest tests/coding_agent/test_cli_pipeline.py -v` → PASS

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: run command uses PipelineAdapter with toggle on
    Tool: Bash
    Steps:
      1. Run: USE_PIPELINE=1 AGENT_API_KEY=test uv run python -c "import os; os.environ['USE_PIPELINE']='1'; from coding_agent.core.config import use_pipeline; assert use_pipeline()"
    Expected Result: use_pipeline() returns True
    Failure Indicators: Returns False with env var set
    Evidence: .sisyphus/evidence/task-13-toggle-on.txt

  Scenario: run command falls back to AgentLoop with toggle off
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/coding_agent/test_cli_pipeline.py -v -k "test_run_uses_agent_loop_by_default"
    Expected Result: Without USE_PIPELINE, run creates AgentLoop
    Failure Indicators: PipelineAdapter created without toggle
    Evidence: .sisyphus/evidence/task-13-fallback.txt
  ```

  **Commit**: YES
  - Message: `refactor(cli): wire run command to PipelineAdapter`
  - Files: `src/coding_agent/__main__.py`, `tests/coding_agent/test_cli_pipeline.py`
  - Pre-commit: `uv run pytest tests/coding_agent/ -q`

- [ ] 14. Wire `repl` Command to PipelineAdapter

  **What to do**:
  - RED: Write test in `tests/coding_agent/test_cli_pipeline.py` — with USE_PIPELINE=1, `InteractiveSession` uses PipelineAdapter for multi-turn conversation
  - GREEN: In `src/coding_agent/cli/repl.py`, add Pipeline path in `InteractiveSession.__init__()`:
    - If `use_pipeline()` → create PipelineAdapter, use for all turns
    - PipelineAdapter.run_turn(message) replaces AgentLoop.run_turn(message)
  - RED: Write test for multi-turn — same PipelineAdapter across 2 turns, context preserved
  - GREEN: PipelineAdapter reuses same PipelineContext (with tape) across turns
  - REFACTOR: Ensure slash commands (/plan, /model, /tools, /exit) still work

  **Must NOT do**:
  - Do NOT break slash command handling
  - Do NOT create new PipelineAdapter per turn — reuse across turns for context
  - Do NOT modify the input loop — only the execution path

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Multi-turn state management, slash command preservation, async REPL loop
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 13, 15, 16)
  - **Blocks**: T16, T17
  - **Blocked By**: T6, T10, T12

  **References**:

  **Pattern References**:
  - `src/coding_agent/cli/repl.py:1-196` — FULL InteractiveSession — understand the entire flow: init, run loop, slash commands, error handling, message processing
  - `src/coding_agent/cli/repl.py:140-196` — `_process_message()` — where AgentLoop.run_turn() is called, THE key replacement point

  **API/Type References**:
  - Task 10's PipelineAdapter — Must call run_turn(str) same as AgentLoop
  - Task 12's error recovery — REPL must continue on error

  **Test References**:
  - `tests/cli/test_repl.py`, `tests/cli/test_commands.py` — Existing REPL and CLI command tests for reference patterns

  **WHY Each Reference Matters**:
  - `repl.py` — Must understand full REPL flow to know what to change and what to preserve
  - `_process_message()` — The SINGLE function that needs the AgentLoop→PipelineAdapter swap
  - Error recovery — REPL must handle Pipeline errors gracefully (T12)

  **Acceptance Criteria**:
  - [ ] REPL works with PipelineAdapter (USE_PIPELINE=1)
  - [ ] Multi-turn context preserved (turn 2 sees turn 1's messages)
  - [ ] Slash commands (/plan, /model, /tools, /exit) still work
  - [ ] Streaming works in REPL (not buffered)
  - [ ] Error recovery works (Pipeline error → user can continue)
  - [ ] `uv run pytest tests/coding_agent/test_cli_pipeline.py -v -k repl` → PASS

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: REPL multi-turn context preserved via Pipeline
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/coding_agent/test_cli_pipeline.py -v -k "test_repl_multiturn_context"
    Expected Result: Second turn's tape contains first turn's messages
    Failure Indicators: Empty tape on second turn or context lost
    Evidence: .sisyphus/evidence/task-14-multiturn.txt

  Scenario: REPL slash commands work with Pipeline path
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/coding_agent/test_cli_pipeline.py -v -k "test_repl_slash_commands"
    Expected Result: /plan, /model, /tools return expected output without errors
    Failure Indicators: Command not recognized or error thrown
    Evidence: .sisyphus/evidence/task-14-slash-commands.txt
  ```

  **Commit**: YES
  - Message: `refactor(cli): wire repl command to PipelineAdapter`
  - Files: `src/coding_agent/cli/repl.py`, `tests/coding_agent/test_cli_pipeline.py`
  - Pre-commit: `uv run pytest tests/coding_agent/ -q`

- [ ] 15. Wire Headless Mode to PipelineAdapter

  **What to do**:
  - RED: Write test — with USE_PIPELINE=1, headless mode uses PipelineAdapter with HeadlessConsumer
  - GREEN: In `__main__.py:_run_headless()`, add Pipeline branch: create PipelineAdapter with HeadlessConsumer as WireConsumer
  - REFACTOR: Verify HeadlessConsumer receives same WireMessages as before

  **Must NOT do**:
  - Do NOT modify HeadlessConsumer — it should work unchanged with PipelineAdapter's WireMessages

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Headless path wiring, similar to T13 but different consumer
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 13, 14, 16)
  - **Blocks**: T17
  - **Blocked By**: T6, T10

  **References**:

  **Pattern References**:
  - `src/coding_agent/__main__.py` — `_run_headless()` function
  - `src/coding_agent/ui/headless.py:1-78` — HeadlessConsumer — prints WireMessages to stdout

  **WHY Each Reference Matters**:
  - `_run_headless()` — Where to add the Pipeline branch (mirrors T13 for TUI)
  - `headless.py` — Verify it handles same WireMessage types PipelineAdapter emits

  **Acceptance Criteria**:
  - [ ] `USE_PIPELINE=1 uv run python -m coding_agent run --goal "echo test" --no-tui` → works via Pipeline
  - [ ] Output printed to stdout (not TUI)
  - [ ] `uv run pytest tests/coding_agent/test_cli_pipeline.py -v -k headless` → PASS

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Headless mode uses PipelineAdapter
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/coding_agent/test_cli_pipeline.py -v -k "test_headless_pipeline"
    Expected Result: HeadlessConsumer receives WireMessages from PipelineAdapter
    Failure Indicators: No output or AgentLoop used despite toggle
    Evidence: .sisyphus/evidence/task-15-headless.txt
  ```

  **Commit**: YES (groups with T13)
  - Message: `refactor(cli): wire headless mode to PipelineAdapter`
  - Files: `src/coding_agent/__main__.py`, `tests/coding_agent/test_cli_pipeline.py`
  - Pre-commit: `uv run pytest tests/coding_agent/ -q`

- [ ] 16. Interactive Approval via DirectiveExecutor in REPL

  **What to do**:
  - RED: Write test — in REPL mode, when tool requires approval, DirectiveExecutor's AskUser blocks and waits for user input
  - GREEN: Wire `DirectiveExecutor.ask_user_handler` to REPL's input mechanism — when Pipeline needs approval, prompt user in terminal
  - REFACTOR: Ensure batch mode (run command) auto-approves (no blocking)
  - The approval plugin already exists; this task just connects it to the REPL's I/O

  **Must NOT do**:
  - Do NOT redesign the approval flow — port existing behavior 1:1
  - Do NOT add new approval policies — just connect DirectiveExecutor to terminal I/O
  - Do NOT block in headless/batch mode — auto-approve there

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: I/O wiring between DirectiveExecutor and REPL input loop
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (once T14 done)
  - **Parallel Group**: Wave 3 (with Tasks 13, 14, 15)
  - **Blocks**: T17
  - **Blocked By**: T14

  **References**:

  **Pattern References**:
  - `src/agentkit/directive/executor.py` — DirectiveExecutor with `ask_user_handler` — understand how to set the handler
  - `src/coding_agent/plugins/approval.py` — Existing approval plugin — how it triggers AskUser

  **API/Type References**:
  - `src/agentkit/directive/executor.py` — `AskUser` directive and handler interface

  **WHY Each Reference Matters**:
  - `executor.py` — How to register the ask_user_handler callback
  - `approval.py` — When AskUser is triggered (which tools, what policy)

  **Acceptance Criteria**:
  - [ ] REPL mode: dangerous tool → user prompted for approval in terminal
  - [ ] Batch mode: dangerous tool → auto-approved (no blocking)
  - [ ] User can deny approval → tool not executed
  - [ ] `uv run pytest tests/coding_agent/test_cli_pipeline.py -v -k approval` → PASS

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: REPL approval prompts user
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/coding_agent/test_cli_pipeline.py -v -k "test_repl_approval_prompt"
    Expected Result: Mock ask_user_handler is called when dangerous tool triggers
    Failure Indicators: Tool auto-executes without asking
    Evidence: .sisyphus/evidence/task-16-approval.txt

  Scenario: Batch mode auto-approves
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/coding_agent/test_cli_pipeline.py -v -k "test_batch_auto_approve"
    Expected Result: No prompt in batch mode, tool executes automatically
    Failure Indicators: Blocking prompt in batch mode
    Evidence: .sisyphus/evidence/task-16-batch-auto.txt
  ```

  **Commit**: YES
  - Message: `feat(cli): interactive approval via DirectiveExecutor in REPL`
  - Files: `src/coding_agent/cli/repl.py`, `tests/coding_agent/test_cli_pipeline.py`
  - Pre-commit: `uv run pytest tests/coding_agent/ -q`

- [ ] 17. Integration Test: Full run + repl via Pipeline

  **What to do**:
  - Write comprehensive integration tests in `tests/integration/test_pipeline_e2e.py`:
    1. `run` command with mock LLM → TUI receives streaming events → correct output
    2. `repl` with 2-turn conversation → context preserved across turns
    3. Headless mode → stdout output correct
    4. Doom detection triggers after repeated tools
    5. Tool error → REPL recovers
    6. Large tool result → truncated
    7. Parallel tools → execute concurrently
  - These are end-to-end tests using real Pipeline (mocked LLM provider only)
  - This is the verification gate before proceeding to Wave 4

  **Must NOT do**:
  - Do NOT use real LLM API — mock at provider boundary
  - Do NOT skip any scenario — all 7 must pass

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Comprehensive integration testing across all CLI paths and features
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: NO — this is the Wave 3 verification gate
  - **Parallel Group**: Sequential (end of Wave 3)
  - **Blocks**: T18, T19
  - **Blocked By**: T13, T14, T15, T16, T8, T9, T11

  **References**:

  **Pattern References**:
  - `tests/integration/test_e2e.py:1-189` — Existing E2E tests — follow this pattern for mock setup and assertion style

  **API/Type References**:
  - All Wave 1-3 outputs — Pipeline, PipelineAdapter, plugins, CLI wiring

  **Test References**:
  - `tests/integration/test_e2e.py` — The template to extend or parallel

  **WHY Each Reference Matters**:
  - `test_e2e.py` — Proven integration test patterns to replicate

  **Acceptance Criteria**:
  - [ ] 7 integration scenarios all pass
  - [ ] `uv run pytest tests/integration/test_pipeline_e2e.py -v` → 7/7 PASS
  - [ ] `uv run pytest --no-header -q` → 0 new failures across entire suite

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Full pipeline E2E test suite passes
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/integration/test_pipeline_e2e.py -v
    Expected Result: 7 tests pass (run TUI, run headless, repl multi-turn, doom, error recovery, truncation, parallel)
    Failure Indicators: Any test failure
    Evidence: .sisyphus/evidence/task-17-e2e.txt

  Scenario: No regression in existing tests
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/agentkit/ tests/coding_agent/ tests/integration/test_e2e.py --no-header -q
    Expected Result: All previously passing tests still pass
    Failure Indicators: Any previously passing test now fails
    Evidence: .sisyphus/evidence/task-17-regression.txt
  ```

  **Commit**: YES
  - Message: `test(integration): full run+repl via Pipeline end-to-end`
  - Files: `tests/integration/test_pipeline_e2e.py`
  - Pre-commit: `uv run pytest tests/integration/ -q`

- [ ] 18. Migrate Failing tests/tools/ Tests to New Interfaces

  **What to do**:
  - Audit all 21 failing tests in `tests/tools/` — identify exact failure cause per test (likely: old tool return format `exit_code` key vs new format)
  - For each failing test, follow TDD:
    - RED: Understand what the test was verifying (the behavior, not the format)
    - GREEN: Update test to use new tool interface while verifying same behavior
    - REFACTOR: Clean up test code
  - Group by tool type: shell tests, planner tests, file tests, search tests
  - If a test is testing behavior that no longer exists (old-stack-specific), replace with equivalent new behavior test

  **Must NOT do**:
  - Do NOT delete tests without replacement — every deleted test must have a new test covering same behavior
  - Do NOT modify tool implementations to match old tests — update tests to match new implementations
  - Do NOT batch-fix with regex — understand each test individually

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 21 test migrations requiring individual analysis
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4 (with Task 19)
  - **Blocks**: T20
  - **Blocked By**: T17

  **References**:

  **Pattern References**:
  - `tests/tools/` — All failing test files — read each to understand what they test
  - `src/coding_agent/plugins/core_tools.py` — New tool implementations — the correct interface to test against

  **API/Type References**:
  - `src/coding_agent/tools/` — Old tool interfaces — understand what changed
  - `src/agentkit/tools/` — New tool decorator and registry

  **WHY Each Reference Matters**:
  - `tests/tools/` — The tests to migrate — must understand each test's intent
  - `core_tools.py` — The new tool interface that tests should target
  - Old vs new tool modules — Understand the interface change to correctly update tests

  **Acceptance Criteria**:
  - [ ] All 21 tests/tools/ tests either pass or are replaced with equivalent tests
  - [ ] `uv run pytest tests/tools/ --no-header -q` → 0 failures
  - [ ] No test deleted without replacement

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: All tool tests pass
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/tools/ --no-header -q
    Expected Result: 0 failures, all tests pass
    Failure Indicators: Any test failure
    Evidence: .sisyphus/evidence/task-18-tool-tests.txt

  Scenario: Test count maintained or increased
    Tool: Bash
    Steps:
      1. Run: uv run pytest tests/tools/ --co -q 2>/dev/null | wc -l
    Expected Result: Count ≥ 21 (original failing count, may be more with replacements)
    Failure Indicators: Fewer tests than before (tests deleted without replacement)
    Evidence: .sisyphus/evidence/task-18-test-count.txt
  ```

  **Commit**: YES
  - Message: `fix(tests): migrate legacy tool tests to new interfaces`
  - Files: `tests/tools/*.py`
  - Pre-commit: `uv run pytest tests/tools/ -q`

- [ ] 19. Migrate Failing tests/providers/ Tests to New Interfaces

  **What to do**:
  - Audit 7 failing tests in `tests/providers/` — identify failure cause (likely: streaming API changes, old StreamEvent vs new event types)
  - For each failing test:
    - RED: Understand verified behavior
    - GREEN: Update to new provider event interface
    - REFACTOR: Clean up
  - Provider tests for anthropic and openai_compat
  - Also fix `tests/test_e2e_p1.py` (1 failing test)

  **Must NOT do**:
  - Do NOT modify provider implementations — update tests
  - Do NOT delete tests without replacement

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Provider test migration, streaming event format changes
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4 (with Task 18)
  - **Blocks**: T20
  - **Blocked By**: T17

  **References**:

  **Pattern References**:
  - `tests/providers/` — Failing test files
  - `src/coding_agent/providers/` — Provider implementations

  **API/Type References**:
  - `src/coding_agent/providers/base.py` — Old StreamEvent types
  - `src/agentkit/providers/models.py` — New event types
  - Task 2's event adapter — May affect how tests should be structured

  **WHY Each Reference Matters**:
  - `tests/providers/` — The tests to fix
  - Provider implementations — What the tests should be testing against

  **Acceptance Criteria**:
  - [ ] `uv run pytest tests/providers/ --no-header -q` → 0 failures
  - [ ] `uv run pytest tests/test_e2e_p1.py --no-header -q` → 0 failures
  - [ ] No test deleted without replacement

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: All provider tests pass
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/providers/ tests/test_e2e_p1.py --no-header -q
    Expected Result: 0 failures
    Failure Indicators: Any test failure
    Evidence: .sisyphus/evidence/task-19-provider-tests.txt
  ```

  **Commit**: YES
  - Message: `fix(tests): migrate legacy provider tests to new interfaces`
  - Files: `tests/providers/*.py`, `tests/test_e2e_p1.py`
  - Pre-commit: `uv run pytest tests/providers/ tests/test_e2e_p1.py -q`

- [ ] 20. Remove Feature Toggle, Pipeline is Default

  **What to do**:
  - RED: Write test asserting Pipeline path is always taken (no toggle check)
  - GREEN: Remove `use_pipeline()` branching in `__main__.py` and `repl.py` — Pipeline path becomes the only path
  - REFACTOR: Remove `USE_PIPELINE` env var support, `--use-pipeline` flag, and `core/config.py:use_pipeline()` function
  - Delete `tests/coding_agent/test_feature_toggle.py` (no longer needed)

  **Must NOT do**:
  - Do NOT remove old AgentLoop code yet (that's T21) — just remove the toggle
  - Do NOT proceed unless T17 (E2E tests), T18, T19 all pass

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Remove if/else branches + config function, straightforward cleanup
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO — depends on T17, T18, T19
  - **Parallel Group**: Sequential
  - **Blocks**: T21
  - **Blocked By**: T17, T18, T19

  **References**:

  **Pattern References**:
  - Task 6's changes — The toggle code to remove
  - Task 13, 14, 15's changes — The branching code to simplify

  **Acceptance Criteria**:
  - [ ] No `use_pipeline()` calls in codebase
  - [ ] No `USE_PIPELINE` env var handling
  - [ ] Pipeline is the only execution path
  - [ ] `uv run pytest --no-header -q` → 0 failures

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Toggle removed, Pipeline is sole path
    Tool: Bash
    Steps:
      1. Run: uv run python -c "from coding_agent.core import config; assert not hasattr(config, 'use_pipeline')" 2>&1 || echo "PASS: function removed"
      2. Run: grep -r "use_pipeline" src/ | wc -l
    Expected Result: Function doesn't exist, grep returns 0 matches
    Failure Indicators: Function still exists or grep finds matches
    Evidence: .sisyphus/evidence/task-20-toggle-removed.txt
  ```

  **Commit**: YES
  - Message: `refactor: remove USE_PIPELINE toggle, Pipeline is default`
  - Files: `src/coding_agent/__main__.py`, `src/coding_agent/cli/repl.py`, `src/coding_agent/core/config.py`
  - Pre-commit: `uv run pytest --no-header -q`

- [ ] 21. Remove Old Stack Dead Code

  **What to do**:
  - Delete the following files/modules:
    - `src/coding_agent/core/loop.py` — old AgentLoop
    - `src/coding_agent/core/context.py` — old context builder
    - `src/coding_agent/core/tape.py` — old Tape (new one in agentkit)
    - `src/coding_agent/core/doom.py` — old DoomDetector (replaced by plugin T8)
    - `src/coding_agent/core/parallel.py` — old ParallelExecutor (replaced by plugin T9)
    - Any other `core/` files that are no longer imported
  - Update `src/coding_agent/core/__init__.py` to remove exports of deleted modules
  - Run `grep -r "from coding_agent.core" src/` to verify no remaining imports
  - Do NOT delete `coding_agent.core/` directory if other files remain there

  **Must NOT do**:
  - Do NOT delete files that are still imported by non-deleted code
  - Do NOT delete test files in this task (T18/T19 already handled)
  - Do NOT delete wire/ module yet — TUI still uses it

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: File deletion + import verification, mechanical
  - **Skills**: [`verification-before-completion`]

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential
  - **Blocks**: T22
  - **Blocked By**: T20

  **References**:

  **Pattern References**:
  - `src/coding_agent/core/` — Directory listing to identify all files to delete

  **Acceptance Criteria**:
  - [ ] `grep -r "from coding_agent.core.loop" src/` → empty
  - [ ] `grep -r "from coding_agent.core.tape" src/` → empty
  - [ ] `grep -r "from coding_agent.core.context" src/` → empty
  - [ ] `uv run pytest --no-header -q` → 0 failures
  - [ ] Deleted files: loop.py, context.py, tape.py, doom.py, parallel.py (at minimum)

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: No imports reference deleted modules
    Tool: Bash (grep)
    Steps:
      1. Run: grep -r "from coding_agent.core.loop" src/ tests/
      2. Run: grep -r "from coding_agent.core.tape" src/ tests/
      3. Run: grep -r "from coding_agent.core.context" src/ tests/
    Expected Result: All 3 greps return empty (no matches)
    Failure Indicators: Any match found = dangling import
    Evidence: .sisyphus/evidence/task-21-no-imports.txt

  Scenario: All tests still pass after deletion
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest --no-header -q
    Expected Result: 0 failures
    Failure Indicators: ImportError or test failures from missing modules
    Evidence: .sisyphus/evidence/task-21-tests-pass.txt
  ```

  **Commit**: YES
  - Message: `refactor: remove old AgentLoop and core/ modules`
  - Files: Deleted: `src/coding_agent/core/loop.py`, `context.py`, `tape.py`, `doom.py`, `parallel.py`
  - Pre-commit: `uv run pytest --no-header -q`

- [ ] 22. Final Import Cleanup and Unused Code Sweep

  **What to do**:
  - Run full codebase scan for:
    - Unused imports (any import referencing deleted modules)
    - Dead code in `__init__.py` files re-exporting deleted symbols
    - Old tool registration functions that are no longer called (e.g., `register_shell_tools()`)
    - References to old config or constants (MAX_TOOL_RESULT_SIZE if moved to Pipeline config)
  - Clean up README.md architecture section if it references old `core/` layout
  - Verify agentkit general-purpose boundary: no `coding_agent.wire` imports in `src/agentkit/`

  **Must NOT do**:
  - Do NOT refactor working code — only remove dead code
  - Do NOT update documentation beyond fixing incorrect architecture descriptions
  - Do NOT add new features or abstractions

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Cleanup sweep, straightforward
  - **Skills**: [`verification-before-completion`]

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential (final task before verification)
  - **Blocks**: Final verification
  - **Blocked By**: T21

  **References**:

  **Pattern References**:
  - `src/coding_agent/core/__init__.py` — Likely has exports of deleted modules
  - `src/coding_agent/tools/__init__.py` — May have old tool registration exports

  **Acceptance Criteria**:
  - [ ] No unused imports referencing deleted modules
  - [ ] No dead exports in `__init__.py` files
  - [ ] `grep -r "from coding_agent.wire" src/agentkit/` → empty (G1 guardrail)
  - [ ] `uv run pytest --no-header -q` → 0 failures, ≥700 tests

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Agentkit boundary preserved
    Tool: Bash (grep)
    Steps:
      1. Run: grep -r "from coding_agent" src/agentkit/ | grep -v "__pycache__"
      2. Run: grep -r "import coding_agent" src/agentkit/ | grep -v "__pycache__"
    Expected Result: Both return empty — agentkit has zero coupling to coding_agent
    Failure Indicators: Any import from coding_agent inside agentkit
    Evidence: .sisyphus/evidence/task-22-boundary.txt

  Scenario: Full test suite green
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest --no-header -q
    Expected Result: 0 failures, test count ≥700
    Failure Indicators: Any failure or significant test count drop
    Evidence: .sisyphus/evidence/task-22-final-green.txt
  ```

  **Commit**: YES
  - Message: `refactor: final import cleanup and unused code sweep`
  - Files: Various cleanup
  - Pre-commit: `uv run pytest --no-header -q`

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, run command). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `uv run pytest --no-header -q` + type check. Review all changed files for: `as Any`, empty catches, print() in prod, commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names.
  Output: `Tests [PASS/FAIL] | Files [N clean/N issues] | VERDICT`

- [ ] F3. **Real Manual QA** — `unspecified-high`
  Start from clean state. Test: `run --goal "echo hello"` streams to TUI. `repl` multi-turn works. Headless mode prints to stdout. Doom detection triggers on loops. Ctrl+C gracefully stops. Save evidence to `.sisyphus/evidence/final-qa/`.
  Output: `Scenarios [N/N pass] | Integration [N/N] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff. Verify 1:1 — everything in spec was built, nothing beyond spec. Check "Must NOT do" compliance. Detect cross-task contamination. Flag unaccounted changes.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N] | VERDICT`

---

## Commit Strategy

| Phase | Commit Message | Key Files |
|-------|---------------|-----------|
| T1 | `feat(agentkit): add on_event streaming callback to PipelineContext` | pipeline.py, context models |
| T2 | `feat(coding_agent): add provider event adapter StreamEvent→agentkit events` | llm_provider.py |
| T3 | `feat(agentkit): add error handling in Pipeline tool execution` | pipeline.py |
| T4 | `feat(agentkit): add tool result truncation via hook` | pipeline.py or hook |
| T5 | `feat(coding_agent): add TurnOutcome dataclass for Pipeline results` | adapter types |
| T6 | `feat(coding_agent): add USE_PIPELINE feature toggle` | core/config.py |
| T6b | `feat(coding_agent): add file_patch and subagent tools to CoreToolsPlugin` | core_tools.py |
| T7 | `feat(agentkit): emit streaming events during run_model stage` | pipeline.py |
| T8 | `feat(coding_agent): add DoomDetectorPlugin` | doom_detector.py |
| T9 | `feat(agentkit): add parallel tool execution support` | pipeline.py or plugin |
| T10 | `feat(coding_agent): add PipelineAdapter bridging Pipeline→CLI` | adapter.py |
| T11 | `feat(coding_agent): add SessionMetricsPlugin` | metrics.py |
| T12 | `fix(coding_agent): REPL-safe error recovery in PipelineAdapter` | adapter.py |
| T13 | `refactor(cli): wire run command to PipelineAdapter` | __main__.py |
| T14 | `refactor(cli): wire repl command to PipelineAdapter` | repl.py |
| T15 | `refactor(cli): wire headless mode to PipelineAdapter` | __main__.py |
| T16 | `feat(cli): interactive approval via DirectiveExecutor in REPL` | repl.py |
| T17 | `test(integration): full run+repl via Pipeline end-to-end` | test files |
| T18 | `fix(tests): migrate legacy tool tests to new interfaces` | tests/tools/ |
| T19 | `fix(tests): migrate legacy provider tests to new interfaces` | tests/providers/ |
| T20 | `refactor: remove USE_PIPELINE toggle, Pipeline is default` | __main__.py |
| T21 | `refactor: remove old AgentLoop and core/ modules` | core/ deletion |
| T22 | `refactor: final import cleanup and unused code sweep` | various |

---

## Success Criteria

### Verification Commands
```bash
uv run pytest --no-header -q              # Expected: 0 failures, ≥700 tests
uv run python -m coding_agent run --goal "echo hello"  # Expected: TUI streams, executes via Pipeline
grep -r "from coding_agent.core.loop" src/ # Expected: empty
grep -r "from coding_agent.core.tape" src/ # Expected: empty
grep -r "from coding_agent.wire" src/agentkit/ # Expected: empty (G1 guardrail)
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] All tests pass (0 failures)
- [ ] Feature toggle removed (Pipeline is sole path)
- [ ] Old stack code deleted
- [ ] No agentkit → wire coupling
