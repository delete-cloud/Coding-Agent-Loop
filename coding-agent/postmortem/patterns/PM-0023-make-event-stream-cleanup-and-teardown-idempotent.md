---
id: PM-0023
title: Make event stream cleanup and teardown idempotent
status: active
severity: medium
confidence: medium
subsystems:
- http
- session_manager
related_commits:
- 3aec2520b75b4c1581f734d0b954db1e7c24b548
- 7489e9b3116726e395fa9f5cd8fedaff8875f738
related_files:
- src/coding_agent/ui/http_server.py
- src/coding_agent/ui/session_manager.py
- tests/ui/test_http_server.py
- tests/ui/test_http_server_failover.py
- tests/ui/test_session_manager_public_api.py
- tests/ui/test_session_manager_runtime.py
release_checks:
- Run focused teardown and queue-cleanup regressions in `tests/ui/test_http_server_failover.py`, `tests/ui/test_session_manager_public_api.py`, and `tests/ui/test_session_manager_runtime.py` before release.
- Review same-owner close/delete races and post-append cleanup paths for idempotent teardown before shipping.
---

# Summary

make event stream cleanup and teardown idempotent

# Trigger Conditions

- Changes in `/events` disconnect handling, queue cleanup, or session teardown ordering.
- Historical commits: `fix(ui): serialize session teardown races`, `fix(ui): harden event queue cleanup`

# Known Fix Signals

- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/session_manager.py`
- `tests/ui/test_http_server.py`
- `tests/ui/test_http_server_failover.py`
- `tests/ui/test_session_manager_public_api.py`
- `tests/ui/test_session_manager_runtime.py`

# Release Review Checklist

- Run focused teardown and queue-cleanup regressions in `tests/ui/test_http_server_failover.py`, `tests/ui/test_session_manager_public_api.py`, and `tests/ui/test_session_manager_runtime.py` before release.
- Review same-owner close/delete races and post-append cleanup paths for idempotent teardown before shipping.
