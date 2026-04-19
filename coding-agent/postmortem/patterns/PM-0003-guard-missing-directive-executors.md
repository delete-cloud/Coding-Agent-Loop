---
id: PM-0003
title: Guard missing directive executors
status: active
severity: medium
confidence: medium
subsystems:
- adapter
related_commits:
- c606f664a080c5aaeddba30357611cc2e566cc48
related_files:
- src/coding_agent/adapter.py
- tests/coding_agent/test_pipeline_adapter.py
release_checks:
- Run focused tests for adapter changes before release.
- Review affected files for the same control-flow shape before shipping.
---

# Summary

guard missing directive executors

# Trigger Conditions

- Changes in adapter paths
- Historical commit: `fix: guard missing directive executors`

# Known Fix Signals

- `src/coding_agent/adapter.py`
- `tests/coding_agent/test_pipeline_adapter.py`

# Release Review Checklist

- Run focused tests for adapter changes before release.
- Review affected files for the same control-flow shape before shipping.
