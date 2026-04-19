# Postmortem Knowledge Base

This directory stores recurring failure patterns extracted from historical fix commits.

## Contents

- `taxonomy.yaml` — shared classification values
- `index.yaml` — machine-readable pattern index
- `patterns/` — recurring failure patterns
- `onboarding/` — Phase 1 historical ingestion reports
- `templates/` — starter templates for future updates

## Phase 1 Scope

Phase 1 builds a deterministic starting corpus from local git history. It records patterns, affected files, and release review checks that later release automation can reuse.
