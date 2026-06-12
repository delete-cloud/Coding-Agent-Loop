# Contributing

## Main Branch Gate

All changes to `main` must land through a pull request. Do not push directly to
`main`, even for small fixes.

Before merging a pull request, the `CI / test` check must pass. That workflow
runs:

```bash
cd coding-agent
uv sync --all-extras --dev
uv run pytest tests/ -q -ra
```

Ruff checks are still useful local targeted checks for files changed by a PR.
They are not yet a repository-wide required check because the current tree has
pre-existing lint and format debt outside this gate.

Commit messages and pull request descriptions should report verification from a
clean tree that matches the committed content. If a command was run before a
later amend, rerun it or omit the stale result.

## Repository Setting

The repository owner should enable branch protection for `main` in GitHub:

- Require a pull request before merging.
- Require status checks to pass before merging.
- Select `CI / test` as a required status check.
- Block force pushes and direct pushes to `main`.

The workflow in this repository provides the required check. GitHub branch
protection itself is a repository setting, not a tracked file.
