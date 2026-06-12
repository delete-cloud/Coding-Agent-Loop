---
id: PM-0025
title: Keep main green with PR CI gates
status: active
severity: high
confidence: high
subsystems:
  - ci
  - release
related_commits: []
related_files:
  - ../.github/workflows/ci.yml
  - ../CONTRIBUTING.md
  - README.md
  - AGENTS.md
  - postmortem/index.yaml
release_checks:
  - Confirm pull requests run the full CI workflow before merge.
  - Confirm verification evidence comes from a clean tree matching the committed content.
  - Confirm direct pushes to main are blocked by repository branch protection.
---

# Summary

`main` can become red again if changes bypass pull requests or if verification
evidence is copied from a dirty tree that does not match the committed content.
Full-suite local verification is useful, but it is not a durable gate unless the
same checks run as required PR status checks.

# Trigger Conditions

- A change touches CI, release, contribution, branch, or PR workflow files.
- A PR description, commit message, or review claims full-suite results.
- A post-merge failure appears on `main` after a direct push or unprotected merge.

# Release Review Checklist

- Verify `CI / test` exists and runs `uv run pytest tests/ -q -ra`.
- Verify PR evidence was produced after the final commit or amend.
- Verify no direct push to `main` is needed for the change.
- Verify repository settings require pull requests and the CI status check.
