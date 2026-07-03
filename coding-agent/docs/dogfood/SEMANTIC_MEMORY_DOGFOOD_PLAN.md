# o6n Semantic Memory Dogfood Plan

Status: REVIEWED (8-round codex review loop, terminated with 0 P1 / 0 P2; ready to execute)
Date: 2026-07-02

## Goal

Validate the ADR-0072 semantic memory stack (write -> sync -> cross-session
recall) on the live o6n instance through real usage, producing run_id-level
evidence and a ranked defect list.

The o6n instance was rolled to the latest main image on 2026-07-02
(sre-infra PR #180) specifically to enable this dogfood. Live config
(sre-infra values.yaml): `memory.semantic.enabled=true`, backend lancedb,
embedding SiliconFlow `BAAI/bge-m3` (1024-dim); `kb.enabled=true`, corpus
`sre`, `maxDistance: 0.8`, `deferWhenSemanticMemoryHits: true`.

## Pre-known limitations this dogfood must work around

- **L1 — No semantic recall observability.** `SemanticMemoryPlugin`
  (`src/coding_agent/plugins/semantic_memory.py`) emits no retrieval span and
  the developer console exposes no semantic-hit view; rendered recall
  context may carry source/score/evidence but semantic rank is internal
  merge metadata and is not operator-observable at all. Recall
  verification therefore uses **behavioral
  probes** (below), and **rank-ordering verification is out of scope** until
  instrumentation lands. This limitation is itself recorded as defect D0 in
  the final report.
- **L2 — Defer semantics are not live-verifiable.** With
  `deferWhenSemanticMemoryHits=true`, any nonzero `SemanticMemoryPlugin`
  grounding hit (topic results + accepted memories, with a matching
  query/tape marker) suppresses KB retrieval entirely (`plugins/kb.py`
  `build_context` early-return) —
  before the `retrieval.kb.search` span would be emitted, and the o6n
  deployment runs with `observability.enabled=false`, so there is no
  span/console surface that can prove KB suppression happened for a given
  run. KB deferral is therefore a **documented limitation, not a pass/fail
  gate** in this dogfood. Alignment/dedup (#656) stays covered by the
  existing unit/integration tests and is not a live validation target.
- **L3 — Recall sources differ, and their scopes differ.** Recall context =
  finalized-topic results (no acceptance needed; recallable from new
  sessions) + accepted reviewed memories (require the accept transition AND
  are session-scoped by design: recall rehydrates an accepted memory only
  for the session that produced it, or legacy unscoped records). Probes
  must state which source they exercise; cross-session probes can only
  validate topic recall. Additionally, topic recall is kind-filtered to the
  runtime source-topic kind `"coding"` (recall query kind comes from the
  current-turn source topic), while accepted-memory recall is NOT filtered
  by topic kind — this asymmetry is what lets the accept-flow probe isolate
  its source (see Phase 1 seed kinds).

## Non-goals and boundaries

- No o6n config changes; no K8s/ArgoCD operations (owned by the SRE repo).
- No new feature code in this repo as part of the dogfood; defects found are
  filed and fixed in separate scoped PRs (ADR first if they cross
  persistence/protocol boundaries).
- Evidence follows the G64-G67 privacy rules (docs/dogfood/GOAL_PROGRESS.md):
  record session_id / run_id / topic_id / candidate_id / counts / pass-fail
  judgments only — no raw prompts, no model output text, no command output,
  no env values, no secrets.
- `remote memory rebuild` (destructive, global) is OUT of scope. It is a
  recovery tool, not a validation step.
- **Seeded-state retention policy:** dogfood-seeded topics write durable
  state that outlives the dogfood: tape anchors, a finalized topic, AND a
  synced semantic vector document. Candidate reject/archive transitions
  only change the reviewed-memory candidate — they do NOT delete the topic
  or its topic-summary vector document. There is no supported operator/API
  cleanup path: a global rebuild re-scans finalized topics from the topic
  store and re-upserts them, so seeded topics SURVIVE rebuild;
  store-level topic deletion exists but is internal-only (dogfood seed
  compensation), with no operator topic-delete + semantic-GC maintenance
  workflow (recorded as pre-known defect **D1**). Seeded content
  must therefore be permanent-quality: real, useful, non-sensitive
  knowledge about this project that is intended to remain in the index
  indefinitely. Every seed's returned `topic_id`/`candidate_id` is
  recorded; at wrap-up each candidate is explicitly dispositioned (accept
  the genuinely useful ones — one of which is the Phase 2 accept-flow probe
  — reject or archive the rest, understanding the vector document stays).

## Access path

The local CLI has a registered remote endpoint `o6n`
(base URL comes from the local remotes config and is intentionally not
recorded here per the public-repo topology boundary, PR #600; user auth =
stored token, admin auth = `admin-token-env`). Interaction uses `python -m coding_agent remote ...`
(run from `coding-agent/`), with two explicit exceptions:

1. Plain HTTP GET to `/healthz` and `/readyz` (no CLI command exists).
2. The review-candidate transition, which has no CLI command:
   `POST /sessions/{session_id}/memory/reviews/{candidate_id}` with
   `{"status": "accepted", "reason": "..."}`, pinned to the same session
   that produced the candidate. Auth is visible-session bearer auth (the
   stored user token is the least-privilege choice; admin also works).
3. **(Added during live execution 2026-07-02, finding F1/D3.)** Session
   creation via `POST /sessions` with an empty JSON body (user bearer
   auth). Discovered live: ANY `workspace_source` (both `--repo` and
   `--empty-workspace`) requires server-side `cloud_workspace.enabled=true`,
   which the o6n deployment does not set — so `remote run`/`remote repl`
   cannot create sessions there at all (they enforce one of the two
   workspace flags). The o6n-supported session mode is no-workspace
   (the webui path). Consequence: sessions are created via this raw POST,
   prompts are sent with `remote prompt o6n <session_id> --goal ...`, and
   Phase 1's git-backed `--repo` preconditions are INAPPLICABLE on o6n —
   Phase 1 prompts are real questions answerable from the KB corpus and
   general context instead of an uploaded repo. Recorded as defect **D3**:
   the remote CLI has no session-create path for no-workspace deployments.

No ssh, no kubectl.

## Phase 0 — Probe (minimal mutation; stop/go gate)

This phase is NOT read-only: creating a probe session runs one real agent
turn, which itself creates durable session/run state and may create a topic.
The probe prompt must therefore be permanent-quality content like every
other prompt in this dogfood (a real, small question about this project).

1. `remote list` sanity; HTTP GET `/healthz` and `/readyz` (expect 200).
2. Create one probe session via a minimal retained run with no workspace
   upload:

       uv run python -m coding_agent remote run o6n \
         --empty-workspace --goal "<small real question>"

   Then run `remote memory status o6n --session <session_id>`.
   Baseline recorded from the actual response fields:
   `document_count`, `reviewed_memory_count`,
   `accepted_reviewed_memory_count`, `topic_store_available` (expect true).
   The status API has no backend/enabled field: semantic-memory-disabled
   manifests as an error/4xx on this route, which is itself the stop signal.
3. `remote memory reviews o6n --session <session_id>` current-state read.

Stop criteria: endpoint unreachable; healthz/readyz non-200; local CLI error
before any HTTP call because the admin-token env var is unset (distinct
signal: fix the local shell, not the server); HTTP 401/403 on user or admin
routes (server-side auth problem); status route errors
in a way indicating semantic memory disabled; `topic_store_available` false.
In that case: report, fix environment, do not proceed.

## Phase 1 — Write path (real tasks create memory)

Command shape (session is retained because `--download` is NOT used):

    uv run python -m coding_agent remote run o6n \
      --repo <git-toplevel-path> --goal "<real question>"

Git-backed `--repo` preconditions: the path must be the Git worktree
top-level (for this repo that is the PARENT of `coding-agent/`), clean, on a
named branch whose HEAD is contained in `origin/<branch>`. A clean
Git-backed worktree is REQUIRED — do NOT use `--snapshot-fallback`: it
archives the raw filesystem tree honoring only a fixed exclude list
(`.git`, `__pycache__`, `*.pyc/pyo`), NOT `.gitignore`, so it would upload
local agent state such as `coding-agent/data/` (tapes) and `.venv/`, and
can trip the archive size caps. If the working tree is dirty, run the
dogfood from a fresh clean clone/worktree instead.

1. Run 2-3 real coding tasks in separate retained sessions (questions about
   the SRE corpus and this repo's architecture that should produce
   informative topics). Do not use `remote repl` (one-shot, deletes the
   session) and do not pass `--download` (also deletes the session).
2. After each run, retrieve and record the run identity BEFORE any judgment:
   `uv run python -m coding_agent remote runs o6n --session <session_id>` —
   record `run_id` and run status. Then `remote memory status o6n --session
   <id>` and record count deltas vs the Phase 0 baseline. Whether ordinary
   remote execution finalizes durable topics at all is finding #1 (ADR-0072
   documents that it is NOT guaranteed).
3. Organic-topic emergence is recorded as a counts-delta observation —
   neither `remote runs` nor `memory status` exposes topic IDs, so organic
   finalized topics WITHOUT review candidates have no CLI-visible anchor
   (this gap feeds D0); when organic candidates do exist,
   `memory reviews` records expose their `topic_id` and may be used as
   optional anchors. Every
   Phase 2 topic-recall probe must therefore anchor to a SEEDED topic with
   a recorded `topic_id`: seed at least one topic per session regardless of
   whether organic topics appeared:

       uv run python -m coding_agent remote memory dogfood-topic o6n \
         --session <session_id> --title "<real title>" --summary "<real summary>"

   Length caps: `--title` <= 256 chars, `--summary` <= 256 chars, kind
   <= 64 chars. Seed kinds are deliberate (see L3): topic-recall probe
   seeds use the default `kind="coding"` (the only kind topic recall can
   return); the ONE seed reserved for the Phase 2 accept-flow probe uses a
   non-coding kind, e.g. `--kind incident` — its topic is excluded from
   topic recall by the kind filter, so any post-accept same-session hit on
   its content can only come from accepted-memory recall (the derived
   candidate kind is `fact` internally, independent of topic kind). Treat the seed response as a gate: non-empty `warnings` =
   FAIL (stop and diagnose); record returned `topic_id` and `candidate_id`;
   re-run `memory status` and require `document_count` to increase —
   unchanged count after a warning-free seed = FAIL. `candidate_id` may be
   null even without warnings (candidate writes are config-gated): the
   Phase 2 accept-flow probe REQUIRES at least one non-empty
   `candidate_id`; if all seeds return null, mark the accept-flow probe
   BLOCKED and diagnose the memory write / candidate settings as a finding
   instead.

## Phase 2 — Recall quality (behavioral probes)

1. **Positive topic-recall probe (source: finalized topics, L3).** In a NEW
   retained session, ask a question answerable only from a recorded Phase 1
   seeded topic's (`topic_id` in evidence)
   distinctive content. Record the probe session's `run_id` via
   `remote runs o6n --session <id>` first. PASS = the answer demonstrably
   uses that content (operator judgment, recorded as pass/fail +
   session_id/run_id, content not copied into evidence). FAIL =
   generic/no-knowledge answer. Rank ordering is NOT judged (L1/D0).
2. **KB-deferral observation (L2 — limitation, NOT a pass/fail gate).**
   Per L2 there is no live signal proving KB suppression for a given run
   (deferral returns before the KB span; o6n observability is off).
   Best-effort only: note behavioral impressions for a query that plausibly
   hits both sources, but record no verdict. The lack of a verifiable
   signal feeds defect D0/D2 (observability gaps).
3. **Negative probe.** In a retained session, ask questions unrelated to
   any seeded/organic topic. Record `session_id` + `run_id` via
   `remote runs o6n --session <id>` BEFORE judging. PASS = no fabricated
   "memory" of nonexistent prior work appears in behavior.
4. **Accept-flow probe (source: accepted memories, L3).** Requires the
   non-empty `candidate_id` of the dedicated NON-CODING-kind Phase 1 seed
   (else BLOCKED, see Phase 1 gate) — that topic is invisible to topic
   recall, so a hit here isolates accepted-memory recall.
   `remote memory reviews o6n --session <phase1_seed_session_id> --status
   candidate` — note: the session here is the Phase 1 session that produced
   the candidate, NOT the new probe session — then accept the recorded
   candidate via the documented raw-HTTP transition and verify via
   `reviews --status accepted` and `accepted_reviewed_memory_count`
   increment. The post-accept recall probe MUST run in that same Phase 1
   seed session (accepted-memory recall is session-scoped, L3; a
   new-session probe would false-fail by design):

       uv run python -m coding_agent remote prompt o6n \
         <phase1_seed_session_id> --goal "<question exercising the accepted memory>"

   followed by `remote runs o6n --session <phase1_seed_session_id>` to
   record the new run_id. Cross-session accepted-memory recall is
   unsupported by design and is NOT tested.

## Phase 3 — Wrap-up

- Disposition every dogfood-created candidate per the retention policy —
  seeded AND organic: list each dogfood session's candidates via
  `remote memory reviews o6n --session <id>` and accept / reject / archive
  every one (each recorded). Organic topic vector documents created by
  dogfood runs are intentionally retained (every dogfood prompt is
  permanent-quality real content by policy, and D1 means they could not be
  removed anyway); this retention decision is recorded in the evidence.
- Evidence written to `docs/dogfood/SEMANTIC_MEMORY_RUN_EVIDENCE.md`, landed
  via PR with the normal CI gate.
- Defect list ranked by severity, pre-seeded with D0 (missing semantic
  recall observability), D1 (no supported operator cleanup workflow for
  finalized topics and their vector documents — they survive global
  rebuild; store-level delete is internal-only), and D2 (KB deferral has
  no verifiable live signal); each small fix is a separate PR
  (implementation delegated per the usual workflow); anything touching
  persistence/protocol/data-model boundaries gets an ADR first.

## Verification commands (run from `coding-agent/`)

- `uv run python -m coding_agent remote list`
- `curl -fsS <o6n-base-url>/healthz` / `.../readyz` (base URL from the local remotes config)
- `uv run python -m coding_agent remote run o6n --repo <git-toplevel> --goal "..."` (clean Git-backed worktree required — `--snapshot-fallback` is forbidden, see Phase 1; use a fresh clone if dirty)
- `uv run python -m coding_agent remote runs o6n --session <session_id>` (record run_id after every run)
- `uv run python -m coding_agent remote prompt o6n <session_id> --goal "..."` (follow-up prompt in an existing session; used by the accept-flow probe)
- `uv run python -m coding_agent remote memory status o6n --session <session_id>`
- `uv run python -m coding_agent remote memory reviews o6n --session <session_id> [--status ...]`
- `uv run python -m coding_agent remote memory dogfood-topic o6n --session <session_id> --title "..." --summary "..." [--kind incident]` (admin; every Phase 1 session seeds at least one; `--kind` non-coding only for the accept-flow seed)
- Review transition (no CLI): `POST /sessions/{session_id}/memory/reviews/{candidate_id}` `{"status": "...", "reason": "..."}` with a session-visible bearer token (user token preferred; never printed).

## Open prerequisites (user-provided)

1. The admin-token env var referenced by the `o6n` remote must be set in the
   executing shell (value never printed or recorded).
2. Explicit authorization to run real sessions against the live single-user
   instance (granted 2026-07-02 in-session).

## Harness usage

Run from `coding-agent/`:

```bash
uv run python docs/dogfood/semantic_memory_dogfood.py phase0 [--session <id>]
uv run python docs/dogfood/semantic_memory_dogfood.py record-run --session <id>
uv run python docs/dogfood/semantic_memory_dogfood.py seed --session <id> --title "..." --summary "..." [--kind coding]
uv run python docs/dogfood/semantic_memory_dogfood.py probe --session <id> --kind topic --judgment pass --note "short operator note"
uv run python docs/dogfood/semantic_memory_dogfood.py transition --session <id> --candidate <cid> --status accepted --reason "short reason"
uv run python docs/dogfood/semantic_memory_dogfood.py status --session <id>
uv run python docs/dogfood/semantic_memory_dogfood.py report
```

The harness writes append-only sanitized JSONL to
`docs/dogfood/semantic_memory_dogfood_evidence.jsonl`; `report` renders
`docs/dogfood/SEMANTIC_MEMORY_RUN_EVIDENCE.md`. It does not send prompts or
execute `remote run` / `remote prompt`; operators run those manually and then
record run rows or judgments.
