# AGENTS.md

## Scope
Applies to the whole repository.

## Project Shape
- Python project (`>=3.12`) using `uv` and `hatchling`
- `src/agentkit/` - reusable runtime/framework code
- `src/coding_agent/` - product-specific coding agent app

Keep the boundary clean: generic runtime behavior belongs in `agentkit`; app behavior belongs in `coding_agent`.

## Setup & Common Commands
```bash
uv sync --all-extras

uv run python -m coding_agent
uv run python -m coding_agent repl
uv run python -m coding_agent run --goal "your goal" --repo .
uv run python -m coding_agent serve --host 127.0.0.1 --port 8080

uv run pytest tests/ -v
uv run pytest tests/agentkit/ -v
uv run pytest tests/coding_agent/ -v
uv run pytest tests/cli/ -v

uv run ruff format src/
```

## Layout
- `src/agentkit/` - framework layer
- `src/coding_agent/` - application layer
- `tests/agentkit/`, `tests/coding_agent/`, `tests/cli/` - primary test targets
- `docs/` - architecture/design docs
- `data/` - local runtime data

## Working Rules
- Prefer minimal, localized changes.
- Reuse existing abstractions before adding new ones.
- If you change `agentkit`, run the relevant `tests/agentkit/` plus impacted `tests/coding_agent/`.
- If you change CLI/entrypoint behavior, run `tests/cli/`.
- Use `README.md` as the default source of truth for workflows unless a more specific doc under `docs/` overrides it.

## ADR & Workflow Rules
- Use ADRs for persistence, protocol, data-model, or cross-module boundary changes, and for decisions with meaningful trade-offs that future readers will ask "why" about.
- Do not write ADRs for straightforward bug fixes, local refactors, or obvious implementation details with no real trade-off.
- Historical spec documents are archived design context only; do not maintain or extend them as living documents.
- Code is the source of truth, and tests are the executable proof that an ADR-backed decision still holds.
- When using an engineer/reviewer/verifier loop, keep it bounded to one `review -> fix -> retest` cycle unless a human explicitly expands the scope.
- If goal, scope, relevant ADRs, and target tests are already known, go straight to the task packet and `.opencode/prompts/README.md`.
- If any of those are still fuzzy, start with `.agents/skills/adr-first-workflow/SKILL.md` first.
- If the task changes persistence, protocol behavior, data model, or cross-module ownership, capture that decision through the ADR workflow before implementation.
- See `docs/adr/README.md` for the ADR format and acceptance-criteria conventions.

## Branch, Base, And Worktree Hygiene
- When a new user request introduces a new feature direction, or clearly diverges from the current branch's purpose, stop and ask whether to continue on the current branch or switch to a new branch/worktree first.
- Prefer a fresh branch/worktree for new feature work that is not a natural continuation of the current branch.
- If the task is still in analysis/design/scoping, ask before starting implementation work on the wrong branch.
- If implementation has already begun on a branch that is no longer the right place for the next feature, finish or shelve that branch intentionally before continuing elsewhere. Preferred path: land the current branch, update the remote base branch, then branch/worktree again from that updated base.
- Do not silently continue unrelated feature development on top of an in-progress branch with a different purpose.
- Evaluate branch cleanliness and PR scope against `origin/main` (or the actual remote base branch), not local `main`/`master`.
- If local `main` is ahead of the remote base, assume GitHub PRs may look polluted until you verify ancestry against the remote base.
- If a feature branch depends on an unmerged shared base, land the shared base first. Do not spend time fixing inherited review comments in the downstream PR.
- When a PR appears to include unrelated files, inspect ancestry first (`git merge-base`, `git log`, `git diff origin/main...HEAD`) before reaching for file-level cherry-picks.
- If downstream PRs need to shrink automatically after a base branch lands, merge the base PR with a merge commit; do not squash-merge or rebase-merge it.

## Notes
- This is a Python/`uv` repo, not an npm repo.
- REPL `!` shell mode uses a real shell; the `bash_run` tool is more restricted. Do not assume shell chaining, pipes, or redirection behave the same way.
- Useful references: `README.md`, `docs/AGENTKIT-ARCHITECTURE.md`, `docs/CODING-AGENT-ARCHITECTURE.md`.
