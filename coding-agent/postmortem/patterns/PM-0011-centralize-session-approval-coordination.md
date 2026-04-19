---
id: PM-0011
title: Centralize session approval coordination
status: active
severity: medium
confidence: medium
subsystems:
- approval
related_commits:
- 07d3b9285aca39090b934059b24bd57011d8d2ef
related_files:
- src/coding_agent/approval/__init__.py
- src/coding_agent/approval/coordinator.py
- src/coding_agent/approval/store.py
- tests/approval/test_coordinator.py
release_checks:
- Run focused tests for approval changes before release.
- Review affected files for the same control-flow shape before shipping.
---

# Summary

centralize session approval coordination

# Trigger Conditions

- Changes in approval paths
- Historical commit: `fix(approval): centralize session approval coordination`

# Known Fix Signals

- `src/coding_agent/approval/__init__.py`
- `src/coding_agent/approval/coordinator.py`
- `src/coding_agent/approval/store.py`
- `tests/approval/test_coordinator.py`

# Release Review Checklist

- Run focused tests for approval changes before release.
- Review affected files for the same control-flow shape before shipping.
