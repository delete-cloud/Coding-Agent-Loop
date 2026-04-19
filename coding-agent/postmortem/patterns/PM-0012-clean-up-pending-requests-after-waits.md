---
id: PM-0012
title: Clean up pending requests after waits
status: active
severity: medium
confidence: medium
subsystems:
- approval
related_commits:
- 2cde0b4a1536c154649cf14ea19b7d5d72f35410
related_files:
- src/coding_agent/approval/store.py
- tests/approval/test_store.py
release_checks:
- Run focused tests for approval changes before release.
- Review affected files for the same control-flow shape before shipping.
---

# Summary

clean up pending requests after waits

# Trigger Conditions

- Changes in approval paths
- Historical commit: `fix(approval): clean up pending requests after waits`

# Known Fix Signals

- `src/coding_agent/approval/store.py`
- `tests/approval/test_store.py`

# Release Review Checklist

- Run focused tests for approval changes before release.
- Review affected files for the same control-flow shape before shipping.
