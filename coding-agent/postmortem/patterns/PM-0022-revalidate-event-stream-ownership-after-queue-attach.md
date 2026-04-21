---
id: PM-0022
title: Revalidate event stream ownership after queue attach
status: active
severity: medium
confidence: medium
subsystems:
- http
- session_manager
related_commits:
- 975149b5e08865afa78df3e2ee3c3f0da4bc6f23
- 18ab2e9f9af9f557073804808728c5cdd5e0c86d
related_files:
- src/coding_agent/ui/http_server.py
- src/coding_agent/ui/session_manager.py
- tests/ui/test_http_server.py
- tests/ui/test_http_server_failover.py
- tests/ui/test_session_manager_runtime.py
release_checks:
- Run focused sticky-owner `/events` regressions in `tests/ui/test_http_server_failover.py` and `tests/ui/test_session_manager_runtime.py` before release.
- Review post-attach owner checks and emit-time ownership assertions for the same stale-owner rejection shape before shipping.
---

# Summary

revalidate event stream ownership after queue attach

# Trigger Conditions

- Changes in `/events` queue attach, event append, or owner-validation paths.
- Historical commits: `fix(ui): close event stream race window`, `fix(ui): reject stale event queue registration`

# Known Fix Signals

- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/session_manager.py`
- `tests/ui/test_http_server.py`
- `tests/ui/test_http_server_failover.py`
- `tests/ui/test_session_manager_runtime.py`

# Release Review Checklist

- Run focused sticky-owner `/events` regressions in `tests/ui/test_http_server_failover.py` and `tests/ui/test_session_manager_runtime.py` before release.
- Review post-attach owner checks and emit-time ownership assertions for the same stale-owner rejection shape before shipping.
