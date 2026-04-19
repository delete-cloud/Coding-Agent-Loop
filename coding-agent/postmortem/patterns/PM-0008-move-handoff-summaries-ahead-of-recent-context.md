---
id: PM-0008
title: Move handoff summaries ahead of recent context
status: active
severity: medium
confidence: medium
subsystems:
- agentkit
related_commits:
- 21636c6e1c3f6929095b61b306f09a46d1952b45
related_files:
- src/agentkit/tape/view.py
- tests/agentkit/tape/test_view.py
release_checks:
- Run focused tests for agentkit changes before release.
- Review affected files for the same control-flow shape before shipping.
---

# Summary

move handoff summaries ahead of recent context

# Trigger Conditions

- Changes in agentkit paths
- Historical commit: `fix(agentkit): move handoff summaries ahead of recent context`

# Known Fix Signals

- `src/agentkit/tape/view.py`
- `tests/agentkit/tape/test_view.py`

# Release Review Checklist

- Run focused tests for agentkit changes before release.
- Review affected files for the same control-flow shape before shipping.
