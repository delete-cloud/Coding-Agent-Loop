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

## Notes
- This is a Python/`uv` repo, not an npm repo.
- REPL `!` shell mode uses a real shell; the `bash_run` tool is more restricted. Do not assume shell chaining/pipes/redirection behave the same way.
- Useful references: `README.md`, `docs/AGENTKIT-ARCHITECTURE.md`, `docs/CODING-AGENT-ARCHITECTURE.md`.
