---
id: PM-0021
title: Guard event stream registration against disappearing sessions
status: active
severity: medium
confidence: medium
subsystems:
- http
- session_manager
related_commits:
- 18ab2e9f9af9f557073804808728c5cdd5e0c86d
- 975149b5e08865afa78df3e2ee3c3f0da4bc6f23
related_files:
- src/coding_agent/ui/http_server.py
- src/coding_agent/ui/session_manager.py
- tests/ui/test_http_server.py
- tests/ui/test_http_server_failover.py
- tests/ui/test_session_manager_public_api.py
release_checks:
- Run focused `/events` registration regressions in `tests/ui/test_http_server.py` and `tests/ui/test_http_server_failover.py` before release.
- Review session lookup and owner-registration paths for the same fail-fast control-flow shape before shipping.
---

# Summary

guard event stream registration against disappearing sessions

# Trigger Conditions

- Changes in `/events` registration, session lookup, or queue attach paths.
- Historical commits: `fix(ui): reject stale event queue registration`, `fix(ui): close event stream race window`

# Known Fix Signals

- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/session_manager.py`
- `tests/ui/test_http_server.py`
- `tests/ui/test_http_server_failover.py`
- `tests/ui/test_session_manager_public_api.py`

# Release Review Checklist

- Run focused `/events` registration regressions in `tests/ui/test_http_server.py` and `tests/ui/test_http_server_failover.py` before release.
- Review session lookup and owner-registration paths for the same fail-fast control-flow shape before shipping.
