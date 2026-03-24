# Repair Phase Minimal Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** When a coding turn produces a directionally correct patch but commands fail, route the next iteration through a specialized repair prompt instead of the full coder. This targets the 16 "repair-eligible" failures identified in the A/B analysis — all had successful patch application followed by `go test` failure, all fixable with mechanical edits.

**Architecture:** No new graph nodes. No new agent struct. Repair is a branch inside the existing `turnNode()` that calls `coder.Repair()` instead of `coder.Generate()` when the previous iteration meets repair criteria. The repair prompt is narrower: it sees the current diff, the failing command output, and is told to do incremental fixes only.

**Key design decisions:**
- Repair uses the same `Coder` struct with a new `Repair(ctx, RepairInput)` method
- Repair gets read-only tools (no `run_command`) — engine controls verification
- Max 1 repair attempt per run (first version) — prevents doom loop
- Engine re-runs the original failing commands after repair, not whatever repair suggests
- `shouldEnterRepair()` is a pure function of session state, not model output

---

### Task 1: Add repair state to loop session

**Files:**
- Modify: `internal/loop/engine_eino.go`

**Step 1: Extend loopPhase**

Add `loopPhaseRepair` to the existing enum:

```go
const (
    loopPhasePlan   loopPhase = "plan"
    loopPhaseCode   loopPhase = "code"
    loopPhaseRepair loopPhase = "repair"
    loopPhaseReview loopPhase = "review"
)
```

**Step 2: Add repair tracking to loopSession**

```go
RepairAttempts     int
RepairEligible     bool
LastFailedCommands []string
LastCommandOutput  string
```

- `RepairAttempts` caps at 1 for first version
- `RepairEligible` is set by `shouldEnterRepair()` at the end of a turn
- `LastFailedCommands` captures which commands failed (for replay after repair)
- `LastCommandOutput` captures the full command output for the repair prompt

**Step 3: Run tests**

```bash
go test ./internal/loop -run 'TestBuildCoderInput|TestEngine'
```

Expected: existing tests pass, new fields are zero-valued and don't affect behavior.

**Step 4: Commit**

```bash
git commit -m "feat: add repair phase state to loop session"
```

---

### Task 2: Add shouldEnterRepair() classification function

**Files:**
- Modify: `internal/loop/engine_eino.go`
- Test: `internal/loop/engine_test.go`

**Step 1: Implement shouldEnterRepair()**

```go
func shouldEnterRepair(st *loopSession, commandFailed bool, patchApplied bool, appliedPatch string) bool
```

Criteria (ALL must be true):
1. `commandFailed == true`
2. `patchApplied == true` (patch was successfully git-applied)
3. `st.RepairAttempts == 0` (haven't tried repair yet)
4. `st.Iteration < st.Spec.MaxIterations` (have iterations budget left)
5. **The applied patch (not the cumulative repo diff) touches at least one goal target file** — use `loopPatchTouchesTargets(appliedPatch, targets, ...)`. This is critical: `mustDiff()` returns the full repo diff which includes changes from prior iterations, so using it would incorrectly trigger repair when a previous turn already touched the target but the current patch went off-target.

Exclusion (ANY means skip repair):
1. `patchApplied == false` (git apply failed — wrong direction)
2. `st.RepairAttempts >= 1` (already tried once)
3. The applied patch does not touch any goal target file (completely off target this turn)

**Step 2: Add tests**

In `engine_test.go`:
- `TestShouldEnterRepairBasicEligible` — patch applied, command failed, 0 attempts → true
- `TestShouldEnterRepairSkipsAfterFirstAttempt` — RepairAttempts=1 → false
- `TestShouldEnterRepairSkipsWhenPatchFailed` — patchApplied=false → false
- `TestShouldEnterRepairSkipsAtMaxIterations` — Iteration >= MaxIterations → false

**Step 3: Run tests**

```bash
go test ./internal/loop -run 'TestShouldEnterRepair'
```

**Step 4: Commit**

```bash
git commit -m "feat: add shouldEnterRepair classification"
```

---

### Task 3: Add Repair method to Coder

**Files:**
- Modify: `internal/agent/coder_eino.go`
- Test: `internal/agent/agent_test.go`

**Step 1: Add RepairInput / RepairOutput types**

```go
type RepairInput struct {
    Goal             string         `json:"goal"`
    RepoSummary      string         `json:"repo_summary"`
    CurrentDiff      string         `json:"current_diff"`
    FailedCommands   []string       `json:"failed_commands"`
    CommandOutput    string         `json:"command_output"`
    PlanSummary      string         `json:"plan_summary,omitempty"`
    PlanSteps        []string       `json:"plan_steps,omitempty"`
    RetrievedContext []kb.SearchHit `json:"retrieved_context,omitempty"`
    RetrievedQuery   string         `json:"retrieved_query,omitempty"`
}
```

RepairOutput reuses `CoderOutput` — same patch/commands/citations/notes contract.

**Step 2: Add repairPrompts()**

System prompt key constraints:
- The current diff already contains correct progress. Do NOT rewrite from scratch.
- Only fix the specific compilation or test failures shown in command_output.
- Patch must be incremental — touch only the files/functions causing the failure.
- Do not add unrelated helpers, cleanup, or refactoring.
- Do not change code that is already passing tests.
- Common repair patterns: add missing struct fields in test data, implement stub methods, fix import paths, fix variable shadowing.

**Step 3: Extract shared Eino agent helper, then implement Repair()**

Current state: `generateWithEino()` and `planWithEino()` each hardcode their tool builder and prompt builder. Adding a third copy for repair would be pure duplication. Before implementing `Repair()`, extract a small shared helper:

```go
func (c *Coder) runStructuredAgent(ctx context.Context, toolMode tools.ToolMode, systemPrompt, userPrompt string, maxStep int) (string, error)
```

This helper handles: create model → build tools for mode → create ReAct agent → generate → return raw content. Then `generateWithEino`, `planWithEino`, and `repairWithEino` all delegate to it with their respective tool mode and prompts.

This is a small refactor (extract, not rewrite) and should be done as the first sub-step of Task 3 to keep the repair implementation clean.

**Step 4: Implement Repair()**

```go
func (c *Coder) Repair(ctx context.Context, in RepairInput) (CoderOutput, error)
```

Implementation:
- Uses `runStructuredAgent(..., ToolModeRepair, repairPrompts(in), 12)`
- Decodes result with existing `decodeCoderOutput()` (same output contract)
- On failure, return empty patch (don't fallback to heuristic — let engine handle it)
- Add `repairHookForTests` for test stubbing

**Step 5: Add ToolModeRepair**

In `internal/tools/eino_tools.go`, add `ToolModeRepair` to the existing enum. It maps to the same read-only surface as `ToolModePlan` and `ToolModeReview`.

**Step 6: Add tests**

In `agent_test.go`:
- `TestCoderRepairFallback` — client unavailable returns empty patch
- `TestRepairPromptsForbidFullRewrite` — system prompt contains "do NOT rewrite from scratch"
- `TestRepairPromptsIncludeFailedCommands` — user prompt includes command_output

In `eino_tools_test.go`:
- `TestBuildToolsForModeRepairExcludesRunCommand`

**Step 7: Run tests**

```bash
go test ./internal/agent -run 'TestCoder|TestRepair'
go test ./internal/tools -run 'TestBuild'
```

**Step 8: Commit**

```bash
git commit -m "feat: add repair method to coder agent"
```

---

### Task 4: Wire repair into turnNode

**Files:**
- Modify: `internal/loop/engine_eino.go`
- Test: `internal/loop/engine_test.go`

**Step 1: Set repair eligibility at end of turn**

In `turnNode()`, after the command execution loop (around line 717) and before the reviewer call, add:

```go
patchApplied := strings.TrimSpace(coderOut.Patch) != "" && callStatus == "completed"
// ... (after command loop)
if shouldEnterRepair(st, commandFailed, patchApplied, coderOut.Patch) {
    st.RepairEligible = true
    st.LastFailedCommands = collectFailedCommands(cmds, ...)
    st.LastCommandOutput = commandOutput.String()
}
```

**Step 2: Add repair telemetry**

In the repair path, emit tool-call records for observability:
- `repair_start` — when entering repair (input = goal + failed commands summary)
- `repair_meta` — after repair completes (output = patch empty/non-empty, status = completed/error/empty_patch)

This mirrors the existing `coder_start` / `coder_meta` / `planner_meta` pattern and enables post-hoc analysis of "repair triggered but empty patch" vs "repair triggered and produced fix" vs "repair not triggered".

**Step 3: Route through repair at start of turnNode**

At the top of `turnNode()`, after the iteration increment, add:

```go
if st.RepairEligible && st.RepairAttempts == 0 {
    st.Phase = loopPhaseRepair
    st.RepairAttempts++
    st.RepairEligible = false
    // call coder.Repair instead of coder.Generate
    repairIn := buildRepairInput(st, currentDiff)
    coderOut, err = e.coder.Repair(coderCtx, repairIn)
    // ... same patch apply + command run flow
}
```

**Important — forced command override:** In the repair path, after `coder.Repair()` returns, the engine MUST ignore `coderOut.Commands` and force `cmds = mergeCommands(st.Commands)`. This is because the current `turnNode` logic (line 666-670) prefers `coderOut.Commands` over spec commands when non-empty. Without this override, the repair model can return a narrower command set (e.g. only the failing test) and shrink the verification scope, defeating the safety guarantee. Add an explicit `isRepairTurn` flag that gates this override:

```go
if isRepairTurn {
    cmds = mergeCommands(st.Commands)  // always use spec commands, ignore model suggestion
} else {
    cmds = sanitizeShellCommands(coderOut.Commands)
    if len(cmds) == 0 {
        cmds = mergeCommands(st.Commands)
    }
}
```

**Step 4: Add buildRepairInput()**

```go
func buildRepairInput(st *loopSession, diff string) agentpkg.RepairInput
```

Maps session state to RepairInput. Includes current diff, failed commands, command output, plan context if available.

**Step 5: Add tests**

In `engine_test.go`:
- `TestTurnNodeRoutesToRepairWhenEligible` — stub coder with repair hook, verify Repair() called instead of Generate()
- `TestTurnNodeSkipsRepairAfterFirstAttempt` — RepairAttempts=1, verify Generate() called
- `TestRepairReRunsAllCommands` — verify engine runs all spec commands after repair, not just failed ones
- `TestRepairForcesSpecCommands` — verify repair path ignores `coderOut.Commands` and uses `mergeCommands(st.Commands)`

**Step 6: Run tests**

```bash
go test ./internal/loop -run 'TestTurnNode|TestRepair|TestShouldEnterRepair'
```

**Step 7: Commit**

```bash
git commit -m "feat: wire repair phase into turn node"
```

---

### Task 5: Verify full scope

**Step 1: Run all package tests**

```bash
go test ./internal/tools ./internal/agent ./internal/loop ./internal/model
```

**Step 2: Run cross-package smoke**

```bash
go test ./... -run 'TestEngineRunDryRun|TestBuildCoderToolsIncludesRunCommand|TestBuildReviewerToolsReadOnlySurface|TestBuildPlannerToolsReadOnlySurface|TestBuildToolsForModeRepairExcludesRunCommand|TestShouldEnterRepair|TestTurnNodeRoutesToRepairWhenEligible'
```

**Step 3: Run full suite**

```bash
go test ./...
```

**Step 4: Commit**

```bash
git commit -m "test: verify repair phase integration"
```

---

## Scope Guards

- Do not add a separate RepairAgent struct or a new graph node.
- Do not let repair agent call run_command — engine controls verification.
- Do not allow more than 1 repair attempt per run in this version.
- Do not change reviewer behavior — reviewer still evaluates the final state.
- Do not modify plan mode behavior in this change.
- Repair prompt must NOT instruct the model to rewrite from scratch.

## Acceptance Criteria

- When a turn ends with patch-applied + command-failed + first attempt, the next turn routes through `coder.Repair()` with narrower prompt.
- Engine re-runs ALL spec commands after repair, not just the previously failed ones.
- Repair failures do not crash the run — they fall through to normal request_changes flow.
- `shouldEnterRepair()` correctly excludes: patch-apply failures, off-target patches, second attempts, max-iterations reached.
- Existing plan mode, reviewer, and finish flows are unaffected.
- All 13 Go packages pass.
