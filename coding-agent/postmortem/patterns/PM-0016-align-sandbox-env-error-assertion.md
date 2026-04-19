---
id: PM-0016
title: Align sandbox env error assertion
status: active
severity: medium
confidence: medium
subsystems:
- bootstrap
related_commits:
- 959e1a830fad351d5db7bc80c737d2d061f1a855
related_files:
- tests/tools/test_shell.py
release_checks:
- Run focused tests for bootstrap changes before release.
- Review affected files for the same control-flow shape before shipping.
---

# Summary

align sandbox env error assertion

# Trigger Conditions

- Changes in bootstrap paths
- Historical commit: `fix(bootstrap): align sandbox env error assertion`

# Known Fix Signals

- `tests/tools/test_shell.py`

# Release Review Checklist

- Run focused tests for bootstrap changes before release.
- Review affected files for the same control-flow shape before shipping.
