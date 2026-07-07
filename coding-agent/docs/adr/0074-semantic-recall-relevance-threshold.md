# ADR-0074: Semantic recall relevance floors and KB-deferral integrity

**Status**: Accepted
**Date**: 2026-07-03

## Context

The 2026-07-02 o6n semantic-memory dogfood
(`docs/dogfood/SEMANTIC_MEMORY_RUN_EVIDENCE.md`) produced snapshot-level
evidence of a recall-quality defect pair:

- **D7 — no relevance floor.** `SemanticMemoryPlugin` injects top-K
  cross-topic recall for ANY query. Message snapshots across three live runs
  show injected topic summaries with rendered scores 0.095–0.143 on queries
  entirely unrelated to those topics (restic/backup and Redis questions
  received deployment-log topics). Two score kinds feed the plan with no
  floor on either: semantic hits carry `l2_distance_to_similarity_v1` =
  `1/(1+L2)` (`topics/semantic_backends/lancedb.py:366`; 0.1429 ≈ L2 6.0),
  and deterministic topic-range hits carry token-overlap fraction
  (`topics/range_index.py:374`; 0.1429 = 1/7 tokens).
- **D8 — defer-on-any-hit suppresses KB permanently.** With
  `[kb].defer_when_semantic_memory_hits = true` (live on o6n), KBPlugin
  skips retrieval whenever the semantic grounding marker's hit count is
  nonzero (`plugins/kb.py:146`). Because D7 makes the hit count nonzero for
  every query, KB retrieval never runs: all three dogfood snapshots contain
  a `Cross-topic recall` section and zero `KB references` sections, although
  the sre KB corpus verifiably contains the queried facts. Net effect:
  enabling semantic memory strictly degraded answer quality by replacing
  relevant KB grounding with irrelevant topic noise.

The KB side of this class of defect was already fixed in June
(`[kb].max_distance`, commit 4225b53); the semantic recall side has no
equivalent, and via the defer coupling it disables the KB fix entirely.

## Decision

Add **default-off per-source relevance floors** to semantic recall, applied
inside the recall planning path **before the plan is assembled**, so the
KB-deferral marker only counts hits that passed the floors:

1. `[memory.semantic].recall_min_score` (float, 0–1, default unset = off):
   minimum `MemoryHit`/topic-result similarity for all **semantic-sourced**
   results on the `l2_distance_to_similarity_v1` scale: topic summaries and
   accepted-memory hits. Score-less accepted-memory records from the
   session-local store listing path are exempt because they have no comparable
   vector similarity score.
2. `[memory.semantic].recall_min_overlap` (float, 0–1, default unset = off):
   minimum token-overlap fraction for **deterministic topic-range** results.
3. Filtering lives in the planner path used by `SemanticMemoryPlugin`
   (threaded plugin → `SemanticRecallPlanner`/`TopicRecallPlanner` as
   optional parameters, default `None`). Direct `TopicRecallPlanner`
   consumers (cross-topic API per ADR-0046, Bee flows, recall evaluation)
   are untouched unless they opt in.
4. **D8 follows structurally**: the grounding marker's hit count is computed
   from the filtered plan (`plugins/semantic_memory.py:128`), so a query
   with only sub-floor hits yields hit count 0 and KB retrieval proceeds.
   No change to the defer switch semantics themselves.
5. Helm: `memory.semantic.recallMinScore` / `recallMinOverlap` values
   rendered into `[memory.semantic]` exactly like `kb.maxDistance` →
   `[kb].max_distance`. Chart default: unset (off). The o6n production
   values (private repo) opt in after a calibration probe measures the
   self-recall similarity of a known-relevant seeded topic; initial floors
   are chosen from that calibration, not guessed here.

## Alternatives Rejected

- **Filter in the LanceDB backend on raw `_distance`.** Rejected: covers
  only semantic-sourced hits (deterministic token-overlap noise passes),
  couples the knob to one backend's distance scale, and other backends
  (fake, future adapters) would each need their own filter.
- **Filter at rendering (`recall_context`).** Rejected: the deferral marker
  is set before rendering, so KB suppression would persist (fixes D7's
  visible noise but not D8).
- **Flip `defer_when_semantic_memory_hits` default to false.** Rejected as
  the primary fix: it abandons the intended "memory first, KB fallback"
  design instead of repairing its quality gate; also a deployment-values
  change, not a code fix. Remains available as an operational mitigation.
- **Quality-weighted defer (defer only when hits exceed a defer-specific
  score).** Rejected for now: once sub-floor hits are filtered from the
  plan, hit-count deferral is again meaningful; a second threshold adds a
  knob without evidence it is needed.
- **One shared floor for both score kinds.** Rejected: the two scales are
  different quantities (vector similarity vs token overlap); a shared knob
  invites miscalibration.

## Acceptance Criteria

- [x] `test_recall_min_score_filters_semantic_results` — semantic hits below
  the floor are excluded from `plan.topic_results`.
- [x] `test_recall_min_score_filters_semantic_accepted_memory_hits` —
  semantic accepted-memory hits below the floor are excluded from
  `plan.accepted_memories` and from the grounding marker count.
- [x] `test_recall_min_overlap_filters_deterministic_results` — token-overlap
  results below the floor are excluded.
- [x] `test_subfloor_hits_zero_grounding_marker_and_kb_runs` — a query whose
  hits are all sub-floor yields hit count 0 in the grounding marker and
  KBPlugin performs retrieval (defer does not trigger).
- [x] `test_floors_default_off_preserve_existing_plans` — with both knobs
  unset, plan contents are byte-identical to today (existing consumers and
  Bee/eval paths unaffected).
- [x] `[memory.semantic]` config parsing validates both knobs (number,
  0 <= v <= 1) with clear errors, mirroring `[kb].max_distance` validation.
- [x] Helm render contract: `recallMinScore`/`recallMinOverlap` render into
  `[memory.semantic]`; omitted values render nothing.
- [x] `uv run pytest tests/coding_agent/test_semantic_recall.py tests/coding_agent/plugins/test_semantic_memory.py tests/coding_agent/plugins/test_kb_plugin.py tests/deploy/test_helm_chart.py -q`

Accepted 2026-07-03. Implementation merged in PR #668 (all named tests in
the gating suites, full suite 4045 green). Live acceptance completed the same
day: o6n opted in via sre-infra PRs #182/#183 after a two-round calibration
(round 1 floors 0.4/0.3 were calibrated on token-overlap-scale samples and
the semantic noise band 0.433-0.493 passed; round 2 raised recall_min_score
to 0.5). Final live values: recall_min_score=0.5, recall_min_overlap=0.3.
Regression evidence (docs/dogfood/SEMANTIC_MEMORY_RUN_EVIDENCE.md): the
restic probe now injects zero cross-topic noise and grounds from the sre KB
corpus (Repo references, distances 0.742-0.781), while the relevant seeded
topic still recalls (overlap 0.5333). The round-1 miscalibration came from
reading token-overlap samples as semantic similarity samples. 2026-07-07
addendum: rendered/operator-visible semantic and deterministic topic-recall
scores are now scale-labeled (`similarity` for semantic
`l2_distance_to_similarity_v1`, `overlap` for deterministic token-overlap) to
make that gotcha visible. KB repo-reference distances (L2) still render as
unlabeled scores and remain out of scope for this change; D9 remains separate
(bge-m3 short-summary similarity bands are barely separable: unrelated
0.433-0.450 vs adjacent 0.463-0.493).

## References

- `docs/dogfood/SEMANTIC_MEMORY_RUN_EVIDENCE.md` (D7/D8 evidence)
- `docs/dogfood/SEMANTIC_MEMORY_DOGFOOD_PLAN.md` (L2/L3 limitations, defect ledger)
- `docs/adr/0072-tape-memory-semantic-index-and-enable-switch.md`
- `docs/adr/0046-cross-topic-memory-and-topic-range-search.md`
- `src/coding_agent/plugins/semantic_memory.py`, `src/coding_agent/plugins/kb.py`
- `src/coding_agent/topics/semantic_recall.py`, `src/coding_agent/topics/range_index.py`
- `src/coding_agent/topics/semantic_backends/lancedb.py:366` (similarity formula)
- `src/coding_agent/core/app.py` (`[kb].max_distance` wiring precedent)
- `helm/templates/configmap-agent-config.yaml`
