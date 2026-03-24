# Step 3 A/B Preparation Checklist

## What Step 3 tests

- **3.1**: Reviewer three-layer judgment framework (commit b5c0d95)
- **3.2**: Coder anti-doom-loop prompt rules (commit 670948c)

## Preconditions

- [ ] Step 2 A/B acceptance complete — results collected and reviewed
- [ ] `go test ./...` passes on main
- [ ] Binary rebuilt from latest main: `cd agent-coding-loop && go build -o bin/agent-loop ./cmd/agent-loop`

## Run configuration (same as Step 2)

```
Tasks:          eval/ab/benchmark_tasks.jsonl  (26 tasks)
Arms:           2 (baseline vs candidate)
Trials:         3
Total runs:     26 × 2 × 3 = 156
Max iterations: 2
Strict mode:    on
Provider:       right.codes (same model as Step 2)
```

## Baseline arm

Use the pre-3.1 commit as baseline — revert reviewer and coder prompt changes:

```bash
# Create baseline worktree from the commit before 3.1 (reviewer three-layer judgment).
# b5c0d95 = "feat(agent): add three-layer judgment framework to reviewer prompt (Step 3.1)"
# b5c0d95~1 = the last commit without any Step 3 prompt changes.
git worktree add /tmp/step3-baseline $(git rev-parse b5c0d95~1) -b step3-baseline-tmp

# Build baseline binary
cd /tmp/step3-baseline/agent-coding-loop
go build -o bin/agent-loop ./cmd/agent-loop

# Run baseline
python3 eval/ab/run_ab.py \
    --tasks eval/ab/benchmark_tasks.jsonl \
    --agent-loop-bin ./bin/agent-loop \
    --repo . \
    --strict-mode \
    --max-iterations 2 \
    --trials 3 \
    --db-path /tmp/ab-step3-baseline.state.db \
    --output-dir /tmp/ab-step3-baseline
```

## Candidate arm

Use current main (includes 3.1 + 3.2):

```bash
# Build candidate binary from main
cd /path/to/agent-coding-loop
go build -o bin/agent-loop ./cmd/agent-loop

# Run candidate
python3 eval/ab/run_ab.py \
    --tasks eval/ab/benchmark_tasks.jsonl \
    --agent-loop-bin ./bin/agent-loop \
    --repo . \
    --strict-mode \
    --max-iterations 2 \
    --trials 3 \
    --db-path /tmp/ab-step3-candidate.state.db \
    --output-dir /tmp/ab-step3-candidate
```

## Output directory naming

```
/tmp/ab-step3-baseline/
/tmp/ab-step3-baseline.state.db
/tmp/ab-step3-candidate/
/tmp/ab-step3-candidate.state.db
```

## Result comparison

```bash
# Summarize each arm
python3 eval/k8s/summarize.py \
    --results /tmp/ab-step3-baseline/results.jsonl \
    --output /tmp/step3-baseline-report.md \
    --strict-mode

python3 eval/k8s/summarize.py \
    --results /tmp/ab-step3-candidate/results.jsonl \
    --output /tmp/step3-candidate-report.md \
    --strict-mode

# Combined comparison
python3 eval/k8s/summarize.py \
    --results /tmp/ab-step3-baseline/results.jsonl /tmp/ab-step3-candidate/results.jsonl \
    --output /tmp/step3-combined-report.md \
    --strict-mode
```

## What to look for

### 3.1 (reviewer three-layer judgment)
- Does the reviewer produce fewer false rejects on correct completions?
- Does `strict_reasons` list shrink for completed tasks?
- Is `fallback_approve_forbidden` rate lower?

### 3.2 (anti-doom-loop)
- Do failed tasks fail faster (lower `duration_sec` / fewer iterations)?
- Is `command_fail_count` lower in the candidate arm?
- Does `repair_stage_count` stay the same or decrease?

## Not in scope

- No prompt changes in this round
- No 3.3 repair expansion merge
- No changes to evaluation criteria or strict rules
