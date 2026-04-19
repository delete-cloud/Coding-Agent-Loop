---
id: PM-0004
title: Handle mapping tool results safely
status: active
severity: medium
confidence: medium
subsystems:
- adapter
related_commits:
- d6e2dfbc6d6584e977185d4f44b77f51f31509c0
related_files:
- src/coding_agent/adapter.py
- tests/coding_agent/test_adapter_tool_result.py
release_checks:
- Run focused tests for adapter changes before release.
- Review affected files for the same control-flow shape before shipping.
---

# Summary

handle mapping tool results safely

# Trigger Conditions

- Changes in adapter paths
- Historical commit: `fix(adapter): handle mapping tool results safely`

# Known Fix Signals

- `src/coding_agent/adapter.py`
- `tests/coding_agent/test_adapter_tool_result.py`

# Release Review Checklist

- Run focused tests for adapter changes before release.
- Review affected files for the same control-flow shape before shipping.
