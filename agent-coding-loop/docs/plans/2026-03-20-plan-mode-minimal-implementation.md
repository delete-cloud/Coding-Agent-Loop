# Plan Mode Minimal Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a minimal automatic plan phase before coding so the engine analyzes in read-only mode first, then feeds a structured plan into the existing coder loop.

**Architecture:** Keep the existing `coder -> patch -> commands -> reviewer` loop intact in [`internal/loop/engine_eino.go`](/Users/kina/Code/Agent/Obsidian-RAG-Agent/Coding-Agent-Loop/agent-coding-loop/internal/loop/engine_eino.go). Reuse the existing `Coder` object for planning by adding a second JSON contract and read-only tool mode, instead of introducing a separate planner runtime. Leave one small seam in the tools layer so phases can choose different tool surfaces without a later rewrite.

**Tech Stack:** Go, Eino graph, existing `internal/agent` coder/reviewer clients, existing `internal/tools` tool builders, existing `internal/loop` state/checkpoint flow.

---

### Task 1: Add phase and plan state to loop session

**Files:**
- Modify: `/Users/kina/Code/Agent/Obsidian-RAG-Agent/Coding-Agent-Loop/agent-coding-loop/internal/loop/engine_eino.go`
- Test: `/Users/kina/Code/Agent/Obsidian-RAG-Agent/Coding-Agent-Loop/agent-coding-loop/internal/loop/engine_test.go`

**Step 1: Add explicit phase state**

Add a small phase enum near `loopSession`, for example:

```go
type loopPhase string

const (
	loopPhasePlan   loopPhase = "plan"
	loopPhaseCode   loopPhase = "code"
	loopPhaseReview loopPhase = "review"
)
```

Extend `loopSession` with only the fields needed for MVP:

```go
Phase       loopPhase
PlanSummary string
PlanSteps   []string
PlanRisks   []string
```

Do not add a generic runtime container in this task.

**Step 2: Initialize phase on run start**

When `flowInput` is created in `run(...)`, initialize:

```go
Phase: loopPhasePlan,
```

If a resumed checkpoint already contains a later phase, do not overwrite it during resume.

**Step 3: Add a focused loop-session unit test**

In [`engine_test.go`](/Users/kina/Code/Agent/Obsidian-RAG-Agent/Coding-Agent-Loop/agent-coding-loop/internal/loop/engine_test.go), add a small test that exercises `buildCoderInput(...)` once Task 3 lands and verifies plan fields flow into coder input. Keep this test close to the existing `buildCoderInput` tests instead of creating a new test file.

**Step 4: Run targeted loop tests**

Run:

```bash
go test ./internal/loop -run 'TestBuildCoderInput|TestEngine'
```

Expected: existing loop tests still pass; the new phase-related test passes.

**Step 5: Commit**

```bash
git add internal/loop/engine_eino.go internal/loop/engine_test.go
git commit -m "feat: add loop phase state for plan mode"
```

### Task 2: Add a per-phase tool policy seam

**Files:**
- Modify: `/Users/kina/Code/Agent/Obsidian-RAG-Agent/Coding-Agent-Loop/agent-coding-loop/internal/tools/eino_tools.go`
- Test: `/Users/kina/Code/Agent/Obsidian-RAG-Agent/Coding-Agent-Loop/agent-coding-loop/internal/tools/eino_tools_test.go`

**Step 1: Introduce a tiny tool mode abstraction**

In [`eino_tools.go`](/Users/kina/Code/Agent/Obsidian-RAG-Agent/Coding-Agent-Loop/agent-coding-loop/internal/tools/eino_tools.go), add a small internal enum such as:

```go
type ToolMode string

const (
	ToolModePlan     ToolMode = "plan"
	ToolModeCode     ToolMode = "code"
	ToolModeReview   ToolMode = "review"
)
```

Add one shared builder:

```go
func BuildToolsForMode(repoRoot string, mode ToolMode, reg *skills.Registry, runner *Runner, kbClient *kb.Client) ([]einotool.BaseTool, error)
```

Rules:
- `plan` gets the existing read-only tools only.
- `review` gets the existing read-only tools only.
- `code` gets read-only tools plus `run_command`.

Keep `BuildCoderTools(...)` and `BuildReviewerTools(...)` as thin wrappers to avoid breaking callers.

**Step 2: Add a dedicated planner wrapper**

Add:

```go
func BuildPlannerTools(...) ([]einotool.BaseTool, error)
```

It should simply delegate to `BuildToolsForMode(..., ToolModePlan, ...)`.

This is the seam for future per-phase policy work. Do not add approvals, sandboxing, or external tool registries here.

**Step 3: Add tool-surface tests**

In [`eino_tools_test.go`](/Users/kina/Code/Agent/Obsidian-RAG-Agent/Coding-Agent-Loop/agent-coding-loop/internal/tools/eino_tools_test.go), add:

- `TestBuildPlannerToolsReadOnlySurface`
- `TestBuildToolsForModeCodeIncludesRunCommand`
- `TestBuildToolsForModePlanExcludesRunCommand`

Assert only on tool names and current behavior. Do not over-specify order unless the current tests already rely on order.

**Step 4: Run targeted tools tests**

Run:

```bash
go test ./internal/tools -run 'TestBuild'
```

Expected: planner surface is read-only, coder surface still includes `run_command`, reviewer behavior is unchanged.

**Step 5: Commit**

```bash
git add internal/tools/eino_tools.go internal/tools/eino_tools_test.go
git commit -m "refactor: add phase-aware tool builder seam"
```

### Task 3: Reuse coder as planner with a second JSON contract

**Files:**
- Modify: `/Users/kina/Code/Agent/Obsidian-RAG-Agent/Coding-Agent-Loop/agent-coding-loop/internal/agent/coder_eino.go`
- Test: `/Users/kina/Code/Agent/Obsidian-RAG-Agent/Coding-Agent-Loop/agent-coding-loop/internal/agent/agent_test.go`

**Step 1: Add planning input/output types**

In [`coder_eino.go`](/Users/kina/Code/Agent/Obsidian-RAG-Agent/Coding-Agent-Loop/agent-coding-loop/internal/agent/coder_eino.go), add minimal types:

```go
type PlanInput struct {
	Goal             string
	RepoSummary      string
	PreviousReview   string
	Diff             string
	SkillsSummary    string
	RetrievedContext []kb.SearchHit `json:"retrieved_context,omitempty"`
	RetrievedQuery   string         `json:"retrieved_query,omitempty"`
}

type PlanOutput struct {
	Summary   string   `json:"summary"`
	Steps     []string `json:"steps"`
	Risks     []string `json:"risks"`
	Citations []string `json:"citations"`
}
```

Keep this contract deliberately small. Do not add patch or commands to plan output.

**Step 2: Add `Plan(...)` to `Coder`**

Implement:

```go
func (c *Coder) Plan(ctx context.Context, in PlanInput) (PlanOutput, error)
```

Implementation rules:
- Reuse the same client and retry/fallback style as `Generate(...)`.
- Use `tools.BuildPlannerTools(...)`.
- Add a dedicated `plannerPrompts(in)` function with a strict JSON contract.
- In the system prompt, explicitly forbid returning patches or commands and instruct the model to analyze first, identify target files/functions, expected edit strategy, and key risks.

**Step 3: Keep fallback behavior simple**

If the client is unavailable or planning fails, return a small heuristic plan, for example:
- one summary
- 2-4 generic steps
- empty or best-effort citations

This keeps plan mode from becoming a new availability failure point.

**Step 4: Extend coder input with plan context**

Add one new field to `CoderInput`:

```go
PlanSummary string `json:"plan_summary,omitempty"`
PlanSteps   []string `json:"plan_steps,omitempty"`
```

Do not overload `PreviousReview` with plan text. Keep plan context explicit.

Update `coderPrompts(in)` so the user payload includes the plan fields and the system prompt says the plan is guidance, not authorization to change unrelated files.

**Step 5: Add agent tests**

In [`agent_test.go`](/Users/kina/Code/Agent/Obsidian-RAG-Agent/Coding-Agent-Loop/agent-coding-loop/internal/agent/agent_test.go), add focused tests for:

- planner prompt / JSON decoding path
- fallback plan output when client config is not ready
- coder prompt includes `plan_summary` / `plan_steps` when present

Prefer small prompt-shape / decode tests over brittle full end-to-end agent tests.

**Step 6: Run targeted agent tests**

Run:

```bash
go test ./internal/agent -run 'TestCoder|TestPlan'
```

Expected: planner path works offline, coder prompt still preserves current JSON contract, no existing coder tests regress.

**Step 7: Commit**

```bash
git add internal/agent/coder_eino.go internal/agent/agent_test.go
git commit -m "feat: add planner contract to coder agent"
```

### Task 4: Insert plan node before the coding turn

**Files:**
- Modify: `/Users/kina/Code/Agent/Obsidian-RAG-Agent/Coding-Agent-Loop/agent-coding-loop/internal/loop/engine_eino.go`
- Test: `/Users/kina/Code/Agent/Obsidian-RAG-Agent/Coding-Agent-Loop/agent-coding-loop/internal/loop/engine_test.go`

**Step 1: Add a `planNode` to the graph**

In `buildLoopRunner(...)`, insert a new node before `turn`:

```go
g.AddLambdaNode("plan", compose.InvokableLambda(e.planNode))
g.AddEdge(compose.START, "plan")
g.AddEdge("plan", "turn")
```

Keep the rest of the graph unchanged. Do not create a second branch tree for this MVP.

**Step 2: Implement `planNode(...)`**

`planNode(...)` should:
- no-op if `st` is nil
- no-op if `st.Phase` is already `code` or later (resume safety)
- set `st.Phase = loopPhasePlan`
- build `PlanInput` from current state and diff
- call `e.coder.Plan(...)`
- persist `PlanSummary` / `PlanSteps` / `PlanRisks` onto `st`
- set `st.Phase = loopPhaseCode`

Do not fail the run just because planning failed. Fall back to a heuristic plan and continue.

**Step 3: Feed the plan into coding**

Update `buildCoderInput(...)` so it includes:

```go
PlanSummary: st.PlanSummary,
PlanSteps:   append([]string(nil), st.PlanSteps...),
```

This is the only required handoff for MVP.

**Step 4: Add loop tests for plan integration**

In [`engine_test.go`](/Users/kina/Code/Agent/Obsidian-RAG-Agent/Coding-Agent-Loop/agent-coding-loop/internal/loop/engine_test.go), add tests for:

- `buildCoderInput` includes stored plan fields
- `planNode` moves phase from `plan` to `code`
- `planNode` does not clobber resumed sessions that are already past plan phase

If you need a stubbed planner result, add a narrow test hook to `Coder` for plan only. Keep the hook private to tests and do not generalize it into a new runtime abstraction.

**Step 5: Run loop tests**

Run:

```bash
go test ./internal/loop -run 'TestBuildCoderInput|TestPlanNode|TestEngine'
```

Expected: plan node is wired into the graph state flow without regressing current engine behavior.

**Step 6: Commit**

```bash
git add internal/loop/engine_eino.go internal/loop/engine_test.go
git commit -m "feat: insert plan phase before coding loop"
```

### Task 5: Verify the full minimal scope

**Files:**
- Verify only

**Step 1: Run the focused package tests**

Run:

```bash
go test ./internal/tools ./internal/agent ./internal/loop
```

Expected: all three packages pass.

**Step 2: Run one smoke integration command**

Run:

```bash
go test ./... -run 'TestEngineRunDryRun|TestBuildCoderToolsIncludesRunCommand|TestBuildReviewerToolsReadOnlySurface'
```

Expected: no regressions in the main loop path or tool-surface assumptions.

**Step 3: Manual behavior check**

Run one dry-run scenario against a temporary repo and verify:
- planning happens before code generation
- coder receives non-empty `plan_summary`
- existing reviewer flow still decides completion vs request-changes

If there is no easy UI surface for the plan yet, inspect temporary debug output or extend one existing structured meta record only if necessary for validation.

**Step 4: Commit**

```bash
git add -A
git commit -m "test: verify minimal plan mode rollout"
```

## Scope Guards

- Do not add YAML role definitions.
- Do not add a separate planner service container.
- Do not add approval/sandbox infrastructure.
- Do not add MCP or external tool registry work.
- Do not redesign reviewer flow in this change.
- Do not make plan mode user-interactive in this change.

## Acceptance Criteria

- Every run enters a read-only planning phase before the first coding turn.
- Planning failures do not fail closed; the run still reaches the existing coding loop.
- Coder receives explicit structured plan context, not a blob stuffed into `PreviousReview`.
- Tool selection now has one per-phase seam, but existing coder/reviewer callers keep their current APIs.
- Existing dry-run and engine tests continue to pass.

Plan complete and saved to `docs/plans/2026-03-20-plan-mode-minimal-implementation.md`. Two execution options:

**1. Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

Which approach?
