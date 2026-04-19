---
id: PM-0017
title: Preserve shared bootstrap contracts
status: active
severity: medium
confidence: medium
subsystems:
- bootstrap
related_commits:
- e88cbd15a2bde6c4b2e59213a3ca7e4336f30e54
related_files:
- src/coding_agent/__main__.py
- src/coding_agent/plugins/kb.py
- src/coding_agent/tools/sandbox.py
- tests/tools/test_shell.py
release_checks:
- Run focused tests for bootstrap changes before release.
- Review affected files for the same control-flow shape before shipping.
---

# Summary

preserve shared bootstrap contracts

# Trigger Conditions

- Changes in bootstrap paths
- Historical commit: `fix(bootstrap): preserve shared bootstrap contracts`

# Known Fix Signals

- `src/coding_agent/__main__.py`
- `src/coding_agent/plugins/kb.py`
- `src/coding_agent/tools/sandbox.py`
- `tests/tools/test_shell.py`

# Release Review Checklist

- Run focused tests for bootstrap changes before release.
- Review affected files for the same control-flow shape before shipping.
