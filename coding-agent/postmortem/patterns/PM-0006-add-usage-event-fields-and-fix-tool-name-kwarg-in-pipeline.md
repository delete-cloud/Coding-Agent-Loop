---
id: PM-0006
title: Add usage event fields and fix tool name kwarg in pipeline
status: active
severity: medium
confidence: medium
subsystems:
- agentkit
related_commits:
- 83db1557f9ea5cb4e52851242dc0f92233e3cbaa
related_files:
- src/agentkit/providers/models.py
- src/agentkit/runtime/pipeline.py
release_checks:
- Run focused tests for agentkit changes before release.
- Review affected files for the same control-flow shape before shipping.
---

# Summary

add usage event fields and fix tool name kwarg in pipeline

# Trigger Conditions

- Changes in agentkit paths
- Historical commit: `fix(agentkit): add usage event fields and fix tool name kwarg in pipeline`

# Known Fix Signals

- `src/agentkit/providers/models.py`
- `src/agentkit/runtime/pipeline.py`

# Release Review Checklist

- Run focused tests for agentkit changes before release.
- Review affected files for the same control-flow shape before shipping.
