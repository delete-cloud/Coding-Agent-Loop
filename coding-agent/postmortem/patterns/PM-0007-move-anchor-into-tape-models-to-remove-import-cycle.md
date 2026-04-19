---
id: PM-0007
title: Move Anchor into tape models to remove import cycle
status: active
severity: medium
confidence: medium
subsystems:
- agentkit
related_commits:
- 2ff2d24f695d01dd6a2d93304186630e3b34ecc9
related_files:
- src/agentkit/tape/anchor.py
- src/agentkit/tape/models.py
release_checks:
- Run focused tests for agentkit changes before release.
- Review affected files for the same control-flow shape before shipping.
---

# Summary

move Anchor into tape models to remove import cycle

# Trigger Conditions

- Changes in agentkit paths
- Historical commit: `fix(agentkit): move Anchor into tape models to remove import cycle`

# Known Fix Signals

- `src/agentkit/tape/anchor.py`
- `src/agentkit/tape/models.py`

# Release Review Checklist

- Run focused tests for agentkit changes before release.
- Review affected files for the same control-flow shape before shipping.
