# Release Hardening G38-G45 Implementation Report

Date completed: 2026-05-20

## Summary

G38-G45 added deterministic release-hardening gates without rewriting AgentKit Core or changing durable runtime, context-system, action-safety, CLI, storage, or package semantics. The phase is implemented as documentation, task packets, manifest loading, and focused contract tests.

## Landed Goals

| Goal | PR | Merge commit | Result |
| --- | --- | --- | --- |
| G38 current-state audit and phase ledger | #241 | `4e5dcfa991da5d7f8aa29858c4047ecb967e7c00` | Merged |
| G39 release verification manifest | #243 | `f2e3b79a86b3467ae1983fb4db5fcef89b969dac` | Merged |
| G40 package/import contract smoke | #244 | `f08c882faa18b3f94b1cb354ce087260a17e04ac` | Merged |
| G41 CLI entrypoint contract smoke | #245 | `4380e91f79d10a1b56aff464142dadd50b91cad4` | Merged |
| G41 credential-free follow-up | #246 | `d969f022f2bc0844dadb2efe32a650e0a235f117` | Merged |
| G42 documentation contract checks | #247 | `7f5f1942cb26e1ff2e18b64480a6b8b8630543c6` | Merged |
| G43 observability safety contract | #248 | `ea3553b3636ee19f890ee9ca1dd255cd4655a65c` | Merged |
| G44 package and JSONL compatibility smoke | #249 | `bc4325874a9960b5d8787279e24aca4dafe7824f` | Merged |

## Artifacts

- `docs/release_hardening/CURRENT_STATE.md`
- `docs/release_hardening/GOAL_PROGRESS.md`
- `docs/release_hardening/release-verification.yaml`
- `docs/release_hardening/IMPLEMENTATION_REPORT.md`
- `.opencode/prompts/tasks/release-hardening-g38-current-state.md`
- `.opencode/prompts/tasks/release-hardening-g39-verification-manifest.md`
- `.opencode/prompts/tasks/release-hardening-g40-import-contract.md`
- `.opencode/prompts/tasks/release-hardening-g41-cli-entrypoints.md`
- `.opencode/prompts/tasks/release-hardening-g42-doc-contract.md`
- `.opencode/prompts/tasks/release-hardening-g43-observability-contract.md`
- `.opencode/prompts/tasks/release-hardening-g44-package-jsonl.md`
- `.opencode/prompts/tasks/release-hardening-g45-final-audit.md`

## Contract Tests Added

- `tests/coding_agent/test_release_verification_manifest.py`
- `tests/coding_agent/test_package_import_contract.py`
- `tests/cli/test_entrypoint_contract.py`
- `tests/coding_agent/test_release_documentation_contract.py`
- `tests/coding_agent/test_release_observability_contract.py`
- `tests/coding_agent/test_release_package_jsonl_contract.py`

## Acceptance Audit

- [x] Preserved regression gates are listed in `docs/release_hardening/release-verification.yaml`.
- [x] `agentkit` public import boundaries are guarded from `coding_agent` imports.
- [x] `coding_agent` top-level import keeps heavy dependencies lazy.
- [x] CLI help and default non-interactive entrypoint are available without provider credentials.
- [x] README command examples and source-boundary docs are checked against live CLI/package metadata.
- [x] Observability export and representative span attributes avoid raw prompt/content/message/result/secret/text values.
- [x] Package metadata includes both runtime packages and the `coding-agent` console entrypoint.
- [x] JSONL tape/store compatibility covers legacy handoff anchors, current anchors, append/load behavior, and fold-boundary anchors.
- [x] CodeRabbit was checked on each PR; when rate-limited, local subagent review was used instead.
- [x] G38-G44 worktrees and remote/local branches were cleaned before G45, leaving only the active G45 branch/worktree.

## Final Verification

G45 reran the preserved baseline and release-hardening contract suite:

- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/coding_agent/evaluation/ -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run pytest tests/coding_agent/test_release_verification_manifest.py tests/coding_agent/test_package_import_contract.py tests/cli/test_entrypoint_contract.py tests/coding_agent/test_release_documentation_contract.py tests/coding_agent/test_release_observability_contract.py tests/coding_agent/test_release_package_jsonl_contract.py -v`
- `git diff --check -- .`

## Residual Risks

- The release manifest is a deterministic local contract; it does not automatically execute in CI until wired there.
- Full-repository ruff was intentionally not made a release-hardening requirement because pre-existing unrelated formatting/lint issues may exist outside this phase.
- Package smoke checks validate local packaging metadata and source behavior, not a published artifact from an external package index.
