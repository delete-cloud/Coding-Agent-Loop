# ADR-0027: Add AgentKit session result and artifact reference models

**Status**: Accepted / implemented through PRs #180-#183
**Date**: 2026-05-16
**Decision owner**: repository maintainer / current product owner

## Context

ADR-0024 introduced remote result publication, ADR-0025 made remote session and
workspace metadata durable, and ADR-0026 hardened the Git review flow. Those
decisions intentionally kept Git workspace behavior, remote HTTP APIs, CLI
commands, workspace retention, and provider cleanup in `coding_agent`.

At the same time, ADR-0026 identified a narrower abstraction that is not
specific to a coding workspace product: reducing an agent run into a stable
result model. Current `coding_agent` HTTP result code already extracts a latest
turn from persisted tape entries with `agentkit.tape.extract.TurnTrace`, then
derives final answer, tool activity summary, and failure details in
`src/coding_agent/ui/http_server.py`. That shape is useful beyond remote coding
workspaces.

The next boundary should therefore be small: move provider-neutral result and
artifact reference data models toward `agentkit`, while leaving all Git,
workspace, Docker, retention, HTTP, and CLI behavior in `coding_agent`.

## Decision

Introduce an `agentkit` result abstraction in a future implementation slice.
`agentkit` should own provider-neutral domain models and reducers for session
and turn results. `coding_agent` should adapt those models into its HTTP schemas,
workspace result refs, and CLI output.

The initial `agentkit` result surface should include:

- `TurnResult`
- `SessionResult`
- `VerificationSummary`
- `FailureSummary`
- `ArtifactRef`
- `ResultRef`
- a reducer such as `latest_turn_result(...)` or `result_from_turn_trace(...)`
  over `agentkit.tape.extract.TurnTrace`

The `agentkit` models should be plain dataclasses or similarly lightweight
domain models. They must not depend on FastAPI, Pydantic HTTP schemas,
Docker-specific workspace metadata, Git provider behavior, or `coding_agent`
configuration.

### Session and turn result boundary

`agentkit` may represent:

- session id and optional turn id;
- lifecycle status values supplied by the host application;
- final assistant answer;
- verification/tool activity summary;
- failure summary;
- artifact references produced by the run;
- provider-neutral metadata maps for host applications that need extensions.

`agentkit` should not decide `coding_agent` session ownership, auth visibility,
remote workspace id resolution, rate limits, HTTP response status codes, or CLI
formatting. `coding_agent` remains responsible for adapting the domain result
into `SessionResultResponse`.

### Result references

`ResultRef` should point to a logical result record, not a stored file or
provider export. It is useful when a session result needs to reference a
specific derived record, such as the latest `TurnResult`, a `SessionResult`, a
`VerificationSummary`, or a `FailureSummary`.

A result ref may include:

- stable result id;
- kind, initially values such as `turn_result`, `session_result`,
  `verification_summary`, and `failure_summary`;
- source session id or turn id;
- creation timestamp when known;
- label or short summary;
- links to related `ArtifactRef` or `ResultRef` values for provenance;
- metadata map for host application extensions.

`ResultRef` differs from `ArtifactRef`: a result ref points to a logical result
record that reducers such as `latest_turn_result(...)` or
`result_from_turn_trace(...)` can produce or load; an artifact ref points to an
external artifact or provider-local export such as a patch, archive, branch, PR,
or log.

### Artifact references

`ArtifactRef` should describe a result artifact without knowing how to create
or mutate it. A reference may include:

- stable id;
- kind, initially values such as `diff`, `patch`, `archive`, `log`, `branch`,
  `pull_request`, and `url`;
- title and summary;
- URI or provider-local locator;
- metadata map;
- producing turn id when known.

`agentkit` may define metadata-only protocols for saving and loading artifact
refs by session or turn. Those protocols must store references, not blobs.

`agentkit` must not generate Git diffs, build patches, create archives, push
branches, open PRs, inspect Docker workspaces, or run workspace GC. Those remain
`coding_agent` workspace/provider responsibilities.

### Phase outcomes

`agentkit` may define small provider-neutral phase result types, such as:

- `RunPhase = setup | agent | finalize`
- `PhaseStatus = started | succeeded | failed | skipped`
- `PhaseOutcome`

These types should describe what happened, not how a phase was enforced.
Setup containers, Docker network modes, secret injection, setup commands,
sandbox policy, and two-container phase boundaries remain in `coding_agent`.

### Storage protocols

If storage protocols are added, place them beside existing `agentkit` storage
protocols. They should follow the current protocol style in
`src/agentkit/storage/protocols.py` and remain metadata-only:

- save/load session result metadata;
- save/load/list artifact refs by session id or turn id;
- delete artifact refs when the host application deletes a session.

The protocol must not store workspace files, Git checkouts, archive payloads,
Docker container ids as operational state, or provider cleanup decisions.

## Non-goals

- Do not move Git-backed workspace source into `agentkit`.
- Do not move Git diff, patch, branch publication, or GitHub PR integration into
  `agentkit`.
- Do not move Docker workspace provider behavior into `agentkit`.
- Do not move remote workspace retention, `provider_instance_id`, pin/unpin, or
  GC into `agentkit`.
- Do not move HTTP API schemas, auth, rate limits, or CLI commands into
  `agentkit`.
- Do not make PostgreSQL store workspace files, patches, archives, or Git
  checkouts.
- Do not introduce a generic artifact blob store in this ADR.
- Do not change ADR-0026 Git Review Flow v2 behavior as part of the first
  `agentkit` extraction.

## Consequences

- `agentkit` gets a reusable result model that other agent runtimes can consume
  without depending on the coding workspace product.
- `coding_agent` keeps control of product-specific remote workspace behavior and
  can adapt `agentkit` domain models into HTTP and CLI contracts.
- The first implementation should be low risk because it can wrap existing tape
  extraction rather than changing workspace providers.
- There will be some temporary duplication while `coding_agent` HTTP result
  schemas coexist with `agentkit` result domain models.
- Future durable result refs can converge on `agentkit` metadata contracts while
  provider-local exports still live in `coding_agent`.

## Implementation Status

Implemented in four narrow PRs:

- [#180](https://github.com/delete-cloud/Coding-Agent-Loop/pull/180)
  added `agentkit.result` domain models and `TurnTrace` reducers.
- [#181](https://github.com/delete-cloud/Coding-Agent-Loop/pull/181)
  added the metadata-only `ArtifactStore` protocol and in-memory protocol
  tests.
- [#182](https://github.com/delete-cloud/Coding-Agent-Loop/pull/182)
  adapted `/sessions/{id}/result` to use the `agentkit` reducer while preserving
  the HTTP response shape.
- [#183](https://github.com/delete-cloud/Coding-Agent-Loop/pull/183)
  added an `ArtifactRef`-shaped projection to durable workspace publication
  `result_refs`, while keeping workspace metadata ownership in `coding_agent`.

## Implementation Plan

### PR 1: Add result domain models and reducer tests

- Add `src/agentkit/result/models.py` with `TurnResult`, `SessionResult`,
  `VerificationSummary`, `FailureSummary`, `ArtifactRef`, and `ResultRef`.
- Add `src/agentkit/result/reducers.py` with reducer functions over
  `agentkit.tape.extract.TurnTrace`.
- Add `tests/agentkit/result/test_reducers.py` proving:
  - final assistant output is preserved;
  - tool activity summary is derived from tool calls;
  - no-tool turns produce no verification summary;
  - failure summaries can be represented without coupling to `coding_agent`
    session objects.

### PR 2: Add metadata-only artifact store protocol

- Extend `src/agentkit/storage/protocols.py` or a sibling module with an
  `ArtifactStore` protocol.
- Keep the protocol metadata-only: save/load/list/delete artifact refs by
  session id and optional turn id.
- Add protocol conformance tests or a small in-memory test double proving the
  expected method signatures.

### PR 3: Adapt `coding_agent` session result endpoint

- Update `src/coding_agent/ui/http_server.py` so `/sessions/{id}/result` uses
  `agentkit` reducers for final answer and verification summary.
- Keep `src/coding_agent/ui/schemas.py` as the HTTP schema owner.
- Keep auth, visibility, workspace id, provider name, model name, and failure
  details adaptation in `coding_agent`.
- Preserve existing HTTP response JSON.

### PR 4: Adapt durable result refs gradually

- Map ADR-0025 workspace `result_refs` publication metadata to `ArtifactRef`
  where useful.
- Keep workspace metadata storage in `coding_agent`.
- Do not move existing workspace record schema into `agentkit`.

## Alternatives Rejected

- **Move all ADR-0024 through ADR-0026 result and publication code into
  `agentkit`** — rejected because Git workspaces, remote publication, Docker
  cleanup, and CLI UX are product/control-plane behavior.
- **Keep result reduction entirely in `coding_agent` forever** — rejected
  because extracting final answer, tool activity, and artifact references from a
  turn is runtime-level behavior already rooted in `agentkit.tape.extract`.
- **Put HTTP response schemas in `agentkit`** — rejected because HTTP shape,
  auth visibility, and rate-limit behavior are application contracts.
- **Store artifact blobs in `agentkit`** — rejected because patches, archives,
  logs, and provider exports have provider-specific retention, size, and cleanup
  policies.
- **Extract workspace retention into `agentkit`** — rejected because
  `provider_instance_id`, host-local cleanup, pin/unpin policy, and GC are
  `coding_agent` remote workspace control-plane concerns.

## Acceptance Criteria

- [x] `test_turn_result_from_trace_preserves_final_output`
- [x] `test_turn_result_from_trace_summarizes_tool_activity`
- [x] `test_turn_result_from_trace_omits_verification_without_tools`
- [x] `test_failure_summary_accepts_host_application_details`
- [x] `test_artifact_ref_accepts_provider_neutral_metadata`
- [x] `test_artifact_store_protocol_saves_and_lists_refs`
- [x] `test_session_result_endpoint_uses_agentkit_reducer_without_response_change`
- [x] workspace publication metadata can be represented as `ArtifactRef`
  (`test_publish_branch_persists_workspace_result_refs`)
- [x] `uv run pytest tests/agentkit/result tests/agentkit/storage/test_protocols.py tests/ui/test_http_server.py -k "turn_result or artifact_ref or artifact_store or session_result or publish_branch_persists_workspace_result_refs" -q`
- [x] `uv run basedpyright --level error src/agentkit/result src/agentkit/storage/protocols.py src/coding_agent/ui/http_server.py tests/agentkit/result tests/agentkit/storage/test_protocols.py tests/ui/test_http_server.py`
- [x] `uv run ruff format --check src/agentkit/result src/agentkit/storage/protocols.py src/coding_agent/ui/http_server.py tests/agentkit/result tests/agentkit/storage/test_protocols.py tests/ui/test_http_server.py`
- [x] `uv run ruff check src/agentkit/result src/agentkit/storage/protocols.py src/coding_agent/ui/http_server.py tests/agentkit/result tests/agentkit/storage/test_protocols.py tests/ui/test_http_server.py`

## References

- `docs/adr/0024-remote-result-publication.md`
- `docs/adr/0025-durable-remote-session-and-workspace-retention.md`
- `docs/adr/0026-remote-result-publication-v2-git-review-flow.md`
- `src/agentkit/tape/extract.py`
- `src/agentkit/storage/protocols.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/schemas.py`
- `src/coding_agent/ui/workspace_store.py`
- `tests/ui/test_http_server.py`
- `tests/ui/test_session_persistence.py`
