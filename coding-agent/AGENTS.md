# AGENTS.md

## Scope
Applies to the whole repository.

## Project Shape
- Python project (`>=3.12`) using `uv` and `hatchling`
- `src/agentkit/`: reusable runtime/framework code
- `src/coding_agent/`: product-specific coding agent app

Keep the boundary clean: generic runtime behavior belongs in `agentkit`; app behavior belongs in `coding_agent`.

## Setup
```bash
uv sync --all-extras
```

## Common Commands
```bash
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

## ADR Workflow Rules
- Use ADRs for persistence, protocol, data-model, or cross-module boundary changes, and for decisions with meaningful trade-offs that future readers will ask "why" about.
- Do not write ADRs for straightforward bug fixes, local refactors, or obvious implementation details with no real trade-off.
- Historical spec documents are archived design context only; do not maintain or extend them as living documents.
- Code is the source of truth, and tests are the executable proof that an ADR-backed decision still holds.
- When using an engineer/reviewer/verifier loop, keep it bounded to one `review -> fix -> retest` cycle unless a human explicitly expands the scope.
- See `docs/adr/README.md` for the full ADR format and acceptance-criteria conventions.

## Workflow Entry Points
- Use this file for repository-wide defaults and ADR decision rules.
- Use `.agents/skills/adr-first-workflow/SKILL.md` when the task is not yet fully scoped, when you are unsure whether an ADR is needed, or when you need help choosing target tests and shaping a task packet.
- Use `.opencode/prompts/README.md` and the local prompt set when the task is already scoped and you want to run the bounded engineer/reviewer/verifier loop.

Practical decision rule:
- If goal, scope, relevant ADRs, and target tests are already known, go straight to the task packet and prompt set.
- If any of those are still fuzzy, start with `adr-first-workflow` first.
- If the task changes persistence, protocol behavior, data model, or cross-module ownership, capture that decision through the ADR workflow before implementation.

## Branch And Worktree Hygiene
- When a new user request introduces a new feature direction, or clearly diverges from the current branch's purpose, stop and ask whether to continue on the current branch or switch to a new branch/worktree first.
- Prefer a fresh branch/worktree for new feature work that is not a natural continuation of the current branch.
- If the task is still in analysis/design/scoping, ask before starting implementation work on the wrong branch.
- If implementation has already begun on a branch that is no longer the right place for the next feature, first finish or shelve the current branch intentionally. The preferred path is: commit the current branch's completed work, merge it into `main`/`master`, then create a new branch/worktree from updated `main`/`master` for the new feature.
- Do not silently continue unrelated feature development on top of an in-progress branch with a different purpose.

## Notes
- This is a Python/`uv` repo, not an npm repo.
- REPL `!` shell mode uses a real shell; the `bash_run` tool is more restricted. Do not assume shell chaining/pipes/redirection behave the same way.
- Useful references: `README.md`, `docs/AGENTKIT-ARCHITECTURE.md`, `docs/CODING-AGENT-ARCHITECTURE.md`.
