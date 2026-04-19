---
id: PM-0018
title: Restore child pipeline bootstrap wiring
status: active
severity: medium
confidence: medium
subsystems:
- bootstrap
related_commits:
- eac8ba3965d46863d60945e56182dda932e5b171
related_files:
- src/coding_agent/__main__.py
- src/coding_agent/adapter.py
- src/coding_agent/plugins/core_tools.py
- tests/coding_agent/test_bootstrap.py
release_checks:
- Run focused tests for bootstrap changes before release.
- Review affected files for the same control-flow shape before shipping.
---

# Summary

restore child pipeline bootstrap wiring

# Trigger Conditions

- Changes in bootstrap paths
- Historical commit: `fix(bootstrap): restore child pipeline bootstrap wiring`

# Known Fix Signals

- `src/coding_agent/__main__.py`
- `src/coding_agent/adapter.py`
- `src/coding_agent/plugins/core_tools.py`
- `tests/coding_agent/test_bootstrap.py`

# Release Review Checklist

- Run focused tests for bootstrap changes before release.
- Review affected files for the same control-flow shape before shipping.
