---
id: PM-0014
title: Make approval responses single-shot
status: active
severity: medium
confidence: medium
subsystems:
- approval
related_commits:
- f2406cfe20638ed522214b386f7b8bca0149b1ad
related_files:
- src/coding_agent/approval/coordinator.py
- src/coding_agent/approval/store.py
- tests/approval/test_coordinator.py
- tests/approval/test_store.py
release_checks:
- Run focused tests for approval changes before release.
- Review affected files for the same control-flow shape before shipping.
---

# Summary

make approval responses single-shot

# Trigger Conditions

- Changes in approval paths
- Historical commit: `fix(approval): make approval responses single-shot`

# Known Fix Signals

- `src/coding_agent/approval/coordinator.py`
- `src/coding_agent/approval/store.py`
- `tests/approval/test_coordinator.py`
- `tests/approval/test_store.py`

# Release Review Checklist

- Run focused tests for approval changes before release.
- Review affected files for the same control-flow shape before shipping.
