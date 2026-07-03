# Semantic Memory Dogfood Run Evidence

This report is rendered deterministically from `docs/dogfood/semantic_memory_dogfood_evidence.jsonl`.
It intentionally omits prompts, model output, command stdout/stderr, URLs, environment values, and tokens.

## Summary

- Records: 20
- Sessions: 5
- Runs: 7
- Topics: 0
- Candidates: 0

## Records

| # | Timestamp | Phase | Action | IDs | Counts | Statuses | Judgment | Note |
| - | - | - | - | - | - | - | - | - |
| 1 | 2026-07-02T16:38:13Z | phase0 | health |  |  | healthz=http_200; readyz=http_200 |  |  |
| 2 | 2026-07-02T16:46:58Z | record-run | run-summary | session_id=f946fbc7-3abb-431a-95e2-05b988262151 | run_count=2 |  |  |  |
| 3 | 2026-07-02T16:46:58Z | record-run | run | session_id=f946fbc7-3abb-431a-95e2-05b988262151; run_id=4976d1646e53402c99811ba35c2c3e44; tape_id=307aa4c0-92be-4e70-98ea-c2e0bf75bd44 |  | run=cancelled |  |  |
| 4 | 2026-07-02T16:46:58Z | record-run | run | session_id=f946fbc7-3abb-431a-95e2-05b988262151; run_id=3ede3a377ef64473a6cedb4bb05d01ad; tape_id=307aa4c0-92be-4e70-98ea-c2e0bf75bd44 |  | run=failed |  |  |
| 5 | 2026-07-02T16:46:59Z | probe | negative | session_id=f946fbc7-3abb-431a-95e2-05b988262151; run_id=3ede3a377ef64473a6cedb4bb05d01ad; tape_id=307aa4c0-92be-4e70-98ea-c2e0bf75bd44 |  | run=failed | blocked | note=F3 stuck turn admission after interrupted approval; F4 persistent tape rebind failure after cancel bricks session turns |
| 6 | 2026-07-02T16:50:50Z | record-run | run-summary | session_id=2c602ec5-7351-442d-9de8-a380032409b6 | run_count=2 |  |  |  |
| 7 | 2026-07-02T16:50:50Z | record-run | run | session_id=2c602ec5-7351-442d-9de8-a380032409b6; run_id=7d5f60a0074c40239b92a2705687cef4; tape_id=2f26b177-d6f8-4544-afb4-dc042c8a3110 |  | run=cancelled |  |  |
| 8 | 2026-07-02T16:50:50Z | record-run | run | session_id=2c602ec5-7351-442d-9de8-a380032409b6; run_id=d29c26d73ad141069f2b5dd4ea0325b7; tape_id=2f26b177-d6f8-4544-afb4-dc042c8a3110 |  | run=failed |  |  |
| 9 | 2026-07-02T16:50:52Z | record-run | run-summary | session_id=b3929ac9-4fd6-4cd3-a102-d3473c4c7864 | run_count=1 |  |  |  |
| 10 | 2026-07-02T16:50:52Z | record-run | run | session_id=b3929ac9-4fd6-4cd3-a102-d3473c4c7864; run_id=e13684576fcd423592a0ee6c1525e9f6; tape_id=dc60ec03-2b76-4af5-a3dd-7ac9476f60ca |  | run=completed |  |  |
| 11 | 2026-07-03T01:24:40Z | record-run | run-summary | session_id=52eccb4e-7f18-4bc0-9f6b-42560453eaad | run_count=1 |  |  |  |
| 12 | 2026-07-03T01:24:40Z | record-run | run | session_id=52eccb4e-7f18-4bc0-9f6b-42560453eaad; run_id=21dd34511ba4400abcaad59cbb7b43e2; tape_id=be978bbb-549b-4cdf-ae89-c6180f815a50 |  | run=completed |  |  |
| 13 | 2026-07-03T01:24:41Z | probe | topic | session_id=52eccb4e-7f18-4bc0-9f6b-42560453eaad; run_id=21dd34511ba4400abcaad59cbb7b43e2; tape_id=be978bbb-549b-4cdf-ae89-c6180f815a50 |  | run=completed | fail | note=ad-hoc KB discriminator: sre-corpus restic/volsync fact NOT grounded; no KB and no topic injection observed (F7) |
| 14 | 2026-07-03T01:24:43Z | record-run | run-summary | session_id=46f5ca31-c49b-44f6-a82a-db83f7831e00 | run_count=1 |  |  |  |
| 15 | 2026-07-03T01:24:43Z | record-run | run | session_id=46f5ca31-c49b-44f6-a82a-db83f7831e00; run_id=8cfea4729c3f475f8f1f46e29a23d387; tape_id=41731d4b-bd27-4e15-83ba-65bc5d41924e |  | run=completed |  |  |
| 16 | 2026-07-03T01:24:44Z | probe | negative | session_id=46f5ca31-c49b-44f6-a82a-db83f7831e00; run_id=8cfea4729c3f475f8f1f46e29a23d387; tape_id=41731d4b-bd27-4e15-83ba-65bc5d41924e |  | run=completed | pass | note=no fabricated memory of nonexistent Redis decision; honest no-record answer (F7 also present: no grounding injected) |
| 17 | 2026-07-03T01:56:11Z | probe | topic | session_id=b3929ac9-4fd6-4cd3-a102-d3473c4c7864; run_id=e13684576fcd423592a0ee6c1525e9f6; tape_id=dc60ec03-2b76-4af5-a3dd-7ac9476f60ca |  | run=completed | pass | note=topic recall works; snapshots show irrelevant topics score 0.10-0.14 injected for ALL queries, zero KB sections: no semantic threshold (D7), defer kills KB (D8) |
| 18 | 2026-07-03T02:28:15Z | phase0 | health |  |  | healthz=http_200; readyz=http_200 |  |  |
| 19 | 2026-07-03T02:28:18Z | phase0 | baseline-status | session_id=b3929ac9-4fd6-4cd3-a102-d3473c4c7864 | accepted_reviewed_memory_count=0; document_count=6; reviewed_memory_count=6 | topic_store_available=true |  |  |
| 20 | 2026-07-03T02:28:21Z | phase0 | baseline-review-summary | session_id=b3929ac9-4fd6-4cd3-a102-d3473c4c7864 | review_count=0 |  |  |  |
