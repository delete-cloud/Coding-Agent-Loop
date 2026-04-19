---
id: PM-0020
title: Checkpoint ids in fs store
status: active
severity: medium
confidence: medium
subsystems:
- checkpoint
related_commits:
- 04252bf694892e8bce237f8e03cfff5077d41689
related_files:
- src/agentkit/storage/checkpoint_fs.py
- tests/agentkit/checkpoint/test_service.py
release_checks:
- Run focused tests for checkpoint changes before release.
- Review affected files for the same control-flow shape before shipping.
---

# Summary

validate checkpoint ids in fs store

# Trigger Conditions

- Changes in checkpoint paths
- Historical commit: `fix(checkpoint): validate checkpoint ids in fs store`

# Known Fix Signals

- `src/agentkit/storage/checkpoint_fs.py`
- `tests/agentkit/checkpoint/test_service.py`

# Release Review Checklist

- Run focused tests for checkpoint changes before release.
- Review affected files for the same control-flow shape before shipping.
