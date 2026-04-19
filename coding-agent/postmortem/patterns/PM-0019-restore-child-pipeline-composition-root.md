---
id: PM-0019
title: Restore child pipeline composition root
status: active
severity: medium
confidence: medium
subsystems:
- bootstrap
related_commits:
- 85f7af02a2d7a666177c4cacae78a7be389f1e2a
related_files:
- src/coding_agent/app.py
release_checks:
- Run focused tests for bootstrap changes before release.
- Review affected files for the same control-flow shape before shipping.
---

# Summary

restore child pipeline composition root

# Trigger Conditions

- Changes in bootstrap paths
- Historical commit: `fix(bootstrap): restore child pipeline composition root`

# Known Fix Signals

- `src/coding_agent/app.py`

# Release Review Checklist

- Run focused tests for bootstrap changes before release.
- Review affected files for the same control-flow shape before shipping.
