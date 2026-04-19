---
id: PM-0002
title: Address code review P0/P1 issues
status: active
severity: medium
confidence: medium
subsystems:
- runtime
related_commits:
- c0440ec8e54dbdd7ba0eebf048b904b77eb326b4
- 2aceff41572620e3367112653fcd2aae73a71661
related_files:
- src/coding_agent/agents/subagent.py
- src/coding_agent/core/context.py
- src/coding_agent/core/loop.py
- src/coding_agent/core/tape.py
- src/coding_agent/providers/anthropic.py
- src/coding_agent/providers/base.py
- src/coding_agent/providers/openai_compat.py
- src/coding_agent/tools/planner.py
- src/coding_agent/tools/registry.py
- src/coding_agent/tools/search.py
- src/coding_agent/tools/subagent.py
- src/coding_agent/ui/headless.py
- tests/agents/test_subagent.py
- tests/providers/test_openai_compat.py
- uv.lock
release_checks:
- Run focused tests for runtime changes before release.
- Review affected files for the same control-flow shape before shipping.
---

# Summary

address code review P0/P1 issues

# Trigger Conditions

- Changes in runtime paths
- Historical commit: `fix(p1): address code review P0/P1 issues`

# Known Fix Signals

- `src/coding_agent/agents/subagent.py`
- `src/coding_agent/core/context.py`
- `src/coding_agent/core/loop.py`
- `src/coding_agent/core/tape.py`
- `src/coding_agent/providers/anthropic.py`
- `src/coding_agent/providers/base.py`
- `src/coding_agent/providers/openai_compat.py`
- `src/coding_agent/tools/planner.py`
- `src/coding_agent/tools/registry.py`
- `src/coding_agent/tools/search.py`
- `src/coding_agent/tools/subagent.py`
- `src/coding_agent/ui/headless.py`
- `tests/agents/test_subagent.py`
- `tests/providers/test_openai_compat.py`
- `uv.lock`

# Release Review Checklist

- Run focused tests for runtime changes before release.
- Review affected files for the same control-flow shape before shipping.
