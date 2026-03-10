# Agent Loop Safety Rules

These rules govern safe execution of the agent coding loop.

## Doom Threshold

- The `doom_threshold` parameter must be **>= 2** and **<= 10**.
- Values below 2 must be clamped to 2; values above 10 must be clamped to 10.
- After clamping, log a warning: `"doom_threshold clamped to <value>"`.

## Max Iterations

- `MaxIterations` controls the upper bound on loop cycles per run.
- The hard upper limit is **50**. If a configured value exceeds 50, clamp it to 50 and log a warning: `"MaxIterations exceeded upper bound, clamped to 50"`.
- Setting `MaxIterations` to 0 or negative is invalid and should default to 10.

## Blocked Run Status

- When a run transitions to `"blocked"` status, the run summary **must** begin with the prefix `[BLOCKED] `.
- Example: `"[BLOCKED] Waiting for user input on ambiguous requirement"`.
- Any summary that does not carry this prefix when the status is blocked is non-compliant and must be corrected before persisting.

## Checkpoint Cleanup

- Checkpoints older than **7 days** are eligible for automatic cleanup.
- Cleanup should run at the start of each new agent loop invocation, before any new work begins.
- Deleted checkpoints must be logged at DEBUG level with their age and file path.
- Never delete the most recent checkpoint regardless of age, to ensure recovery is always possible.
