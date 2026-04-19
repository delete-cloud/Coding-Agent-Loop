---
id: PM-0013
title: Clear answered request projections
status: active
severity: medium
confidence: medium
subsystems:
- approval
related_commits:
- feddcecfa02f95fda5ed44ffb6fc2caecf5df70d
related_files:
- src/coding_agent/approval/coordinator.py
- tests/ui/test_http_server.py
release_checks:
- Run focused tests for approval changes before release.
- Review affected files for the same control-flow shape before shipping.
---

# Summary

clear answered request projections

# Trigger Conditions

- Changes in approval paths
- Historical commit: `fix(approval): clear answered request projections`

# Known Fix Signals

- `src/coding_agent/approval/coordinator.py`
- `tests/ui/test_http_server.py`

# Release Review Checklist

- Run focused tests for approval changes before release.
- Review affected files for the same control-flow shape before shipping.
