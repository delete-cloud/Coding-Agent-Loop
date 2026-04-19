# ADR-0013: Adopt Phase 1 postmortem onboarding

**Status**: Proposed
**Date**: 2026-04-19

## Context

This repository has strong signal in historical fix commits, especially around UI session lifecycle, HTTP/session coordination, restore flows, and PostgreSQL persistence boundaries. The commit stream already uses subsystem-oriented subjects such as `fix(ui): ...` and `fix(storage): ...`, which gives us enough structure to bootstrap a postmortem knowledge base from git history.

The repository does not yet have a mature release automation path. That makes a release-time gate premature, while an offline onboarding phase already creates value: it can group recurring failures, record their affected files and tests, and give future release checks a structured starting point.

We want a Phase 1 implementation that is deterministic, reviewable, and cheap to rerun from the repository itself. It should create versioned artifacts under `./postmortem/` from local git history without depending on external AI services or introducing a second source of truth outside the repo.

## Decision

Add a new Phase 1 postmortem onboarding flow to `coding_agent` that scans repository fix history and writes a structured `./postmortem/` knowledge base.

Specifically:

- Add a new CLI command surface at `python -m coding_agent postmortem phase1`.
- Implement deterministic git-history collection and heuristic clustering inside `coding_agent`, keeping the behavior local to the app layer.
- Generate versioned onboarding artifacts under `./postmortem/`, including `README.md`, `taxonomy.yaml`, `index.yaml`, onboarding reports, reusable templates, and a bounded set of initial pattern documents.
- Keep Phase 1 focused on offline onboarding. Release-time matching, workflow gates, and AI-generated incident narratives stay for later phases.

## Alternatives Rejected

- Generate free-form LLM postmortems for every historical fix commit — rejected because Phase 1 benefits from deterministic clustering and reviewable structure.
- Store onboarding output in `data/` — rejected because these artifacts are versioned engineering knowledge, so they belong in the repository tree.
- Delay all work until a release workflow exists — rejected because the repository already has enough fix-history signal to benefit from onboarding now.
- Put the history-mining logic into `agentkit` — rejected because repository-specific git analysis and postmortem artifact layout belong to `coding_agent`.

## Acceptance Criteria

- [ ] Historical fix collection and artifact generation are covered by `test_collect_fix_commits_parses_subjects_and_files_from_git_history` and `test_build_phase1_artifacts_writes_expected_outputs` in `tests/coding_agent/test_postmortem_phase1.py`.
- [ ] The CLI command is covered by `test_postmortem_phase1_command_generates_postmortem_directory` in `tests/cli/test_postmortem.py`.
- [ ] `python -m coding_agent postmortem phase1 --repo . --output-dir postmortem` generates a reviewable `./postmortem/` tree containing onboarding reports and initial pattern documents.
- [ ] `uv run pytest tests/coding_agent/test_postmortem_phase1.py tests/cli/test_postmortem.py -v`

## References

- `src/coding_agent/__main__.py`
- `src/coding_agent/postmortem_phase1.py`
- `tests/coding_agent/test_postmortem_phase1.py`
- `tests/cli/test_postmortem.py`
- `docs/adr/README.md`
- `.opencode/prompts/task-packet.md`
