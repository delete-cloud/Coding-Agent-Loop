---
id: PM-0005
title: Redact user-facing tool result displays
status: active
severity: medium
confidence: medium
subsystems:
- adapter
related_commits:
- 00d1bb6e377f4b427614e4473af40bd29c34e49f
related_files:
- src/coding_agent/adapter.py
- tests/coding_agent/test_adapter_tool_result.py
release_checks:
- Run focused tests for adapter changes before release.
- Review affected files for the same control-flow shape before shipping.
---

# Summary

redact user-facing tool result displays

# Trigger Conditions

- Changes in adapter paths
- Historical commit: `fix(adapter): redact user-facing tool result displays`

# Known Fix Signals

- `src/coding_agent/adapter.py`
- `tests/coding_agent/test_adapter_tool_result.py`

# Release Review Checklist

- Run focused tests for adapter changes before release.
- Review affected files for the same control-flow shape before shipping.
