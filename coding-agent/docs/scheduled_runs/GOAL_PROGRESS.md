# Scheduled Runs Goal Progress

This ledger tracks G85-G92 for the Topic-aware Scheduled Runs / Proactive Signals phase.

## G85_SCHEDULED_RUNS_CURRENT_STATE_MAP

### Before

- Goal id: G85_SCHEDULED_RUNS_CURRENT_STATE_MAP
- Intended files:
  - `docs/scheduled_runs/GOAL_PROGRESS.md`
  - `docs/scheduled_runs/CURRENT_STATE.md`
- Verification commands:
  - `test -f docs/scheduled_runs/CURRENT_STATE.md`
  - `rg -n "Topic|scheduled|proactive|SessionManager|approval|Prometheus|Developer Console" docs/scheduled_runs/CURRENT_STATE.md`
  - `git diff --check -- .`
- Stop criteria:
  - `docs/scheduled_runs/CURRENT_STATE.md` exists and maps existing runtime, topic, safety, workspace, console, observability, and release surfaces for later scheduled/proactive work.
  - No production code changes are made.
  - Deterministic verification commands pass.

### After

- Status: merged via PR #294.
- Changed files:
  - `docs/scheduled_runs/GOAL_PROGRESS.md`
  - `docs/scheduled_runs/CURRENT_STATE.md`
- Tests run:
  - `test -f docs/scheduled_runs/CURRENT_STATE.md`
  - `rg -n "Topic|scheduled|proactive|SessionManager|approval|Prometheus|Developer Console" docs/scheduled_runs/CURRENT_STATE.md`
  - `git diff --check -- .`
- Results:
  - current-state document exists.
  - required current-state terms are present.
  - whitespace diff check: passed.
- Remaining risks:
  - G85 is a state map only. The ADR, durable schedule/proactive signal schema, bounded trigger planning, topic-aware run launch, console/observability integration, and final smoke tests are deferred to G86-G92.

## G86_SCHEDULED_RUNS_ADR

### Before

- Goal id: G86_SCHEDULED_RUNS_ADR
- Intended files:
  - `docs/scheduled_runs/GOAL_PROGRESS.md`
  - `docs/adr/0040-topic-aware-scheduled-runs.md`
- Verification commands:
  - `test -f docs/adr/0040-topic-aware-scheduled-runs.md`
  - `rg -n "Scheduled Run|Proactive Signal|Topic|AgentKit Core|approval|Prometheus|Bee workflow" docs/adr/0040-topic-aware-scheduled-runs.md`
  - `git diff --check -- .`
- Stop criteria:
  - ADR exists and defines topic-aware scheduled run/proactive signal ownership, durable record boundaries, safety policy, bounded trigger behavior, observability/cardinality rules, and out-of-scope Bee/desktop/bridge/multi-agent work.
  - No production code changes are made.
  - Deterministic verification commands pass.

### After

- Status: passed local verification; pending PR.
- Changed files:
  - `docs/scheduled_runs/GOAL_PROGRESS.md`
  - `docs/adr/0040-topic-aware-scheduled-runs.md`
- Tests run:
  - `test -f docs/adr/0040-topic-aware-scheduled-runs.md`
  - `rg -n "Scheduled Run|Proactive Signal|Topic|AgentKit Core|approval|Prometheus|Bee workflow" docs/adr/0040-topic-aware-scheduled-runs.md`
  - `git diff --check -- .`
- Results:
  - ADR exists and includes the required schedule, proactive signal, topic, AgentKit Core, safety, Prometheus, and Bee workflow boundary terms.
  - whitespace diff check: passed.
- Remaining risks:
  - G86 is ADR-only. Durable schema/store, trigger planning, topic-aware launch intents, proactive signal dedupe/cooldown, console/observability integration, and final smoke tests are deferred to G87-G92.
