# Resume Crash Recovery And Structured Output Notes

Status: implemented
Code commit: `80470db`
Last updated: 2026-03-14

## Goal

Harden structured-output parsing across the primary Eino generation paths and define `Resume` as a crash-recovery-only action with explicit checkpoint reconciliation.

## Final Decisions

### 1. Tool-calling output repair must not rerun the whole agent

The original risk was not just malformed JSON. On the coder path, invalid final JSON could cause the entire Eino tool-calling agent to run again, which could replay side-effectful tools such as `run_command`.

The shipped behavior is:

- `Coder.generateWithEino(...)` performs exactly one `rAgent.Generate(...)`.
- `Reviewer.reviewWithEino(...)` performs exactly one `rAgent.Generate(...)`.
- Raw agent output is parsed locally first.
- If parsing fails, repair is delegated to a no-tool JSON repair path via `ClientConfig.RepairJSON(...)`.

This keeps structured-output recovery on the final payload only and avoids re-executing tool calls during repair.

Relevant code:

- `internal/agent/client_eino.go`
- `internal/agent/coder_eino.go`
- `internal/agent/reviewer_eino.go`

### 2. Resume is crash recovery only

`Resume` is intentionally narrow. It is not a generic continuation mechanism for reviewer-requested changes, blocked runs, or arbitrary failed runs.

The shipped behavior is:

- Only persisted `running` runs are eligible for `Resume`.
- `queued`, `needs_changes`, `blocked`, `completed`, and `failed` are rejected.
- If a checkpoint exists for the `run_id`, resume continues from that checkpoint.
- If the checkpoint is missing, the run is failed closed and persisted as `failed`.
- If checkpoint lookup itself errors, the run is also failed closed and persisted as `failed`.

This keeps `Resume` aligned with crash recovery instead of user-directed continuation.

Relevant code:

- `internal/loop/engine_eino.go`

### 3. The "force fresh rerun without checkpoint" design was rejected

An earlier draft proposed treating `running` + missing checkpoint as a fresh restart using the same `run_id`.

That design was rejected for two reasons:

- The repo/worktree may already be dirty from partially executed node work, so "fresh rerun" would not actually restart from a clean state.
- Reusing the same `run_id` would mix old partial DB records with the new execution, polluting audit and evaluation data.

The current implementation therefore fails closed instead of attempting a synthetic restart.

## Verification

The implementation was verified with:

- `go test ./internal/agent ./internal/loop`
- `go test ./internal/loop -run 'TestEngineResumeCheckpointReadErrorFailsClosed|TestEngineResumeRunningWithoutCheckpointFailsClosed|TestEngineResumeRejectsNonRunningRun|TestEngineResumeRunningWithCheckpointUsesCheckpointState'`
- `go test ./...`

Key regression coverage added:

- tool-calling invalid JSON does not regenerate the whole agent
- non-running runs are rejected by `Resume`
- missing checkpoint fails closed and persists `failed`
- checkpoint lookup errors also fail closed and persist `failed`
- checkpoint-backed resume still uses checkpoint state
