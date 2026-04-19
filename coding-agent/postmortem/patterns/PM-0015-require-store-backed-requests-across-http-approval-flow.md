---
id: PM-0015
title: Require store-backed requests across HTTP approval flow
status: active
severity: medium
confidence: medium
subsystems:
- approval
related_commits:
- de91c5f10b06574508c75751571fe435f7cd2006
related_files:
- src/coding_agent/approval/store.py
- src/coding_agent/ui/http_server.py
- src/coding_agent/ui/schemas.py
- src/coding_agent/ui/session_manager.py
- tests/approval/test_store.py
- tests/integration/test_wire_http_integration.py
- tests/ui/test_http_server.py
- tests/ui/test_security.py
- tests/ui/test_session_manager_public_api.py
release_checks:
- Run focused tests for approval changes before release.
- Review affected files for the same control-flow shape before shipping.
---

# Summary

require store-backed requests across HTTP approval flow

# Trigger Conditions

- Changes in approval paths
- Historical commit: `fix(approval): require store-backed requests across HTTP approval flow`

# Known Fix Signals

- `src/coding_agent/approval/store.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/schemas.py`
- `src/coding_agent/ui/session_manager.py`
- `tests/approval/test_store.py`
- `tests/integration/test_wire_http_integration.py`
- `tests/ui/test_http_server.py`
- `tests/ui/test_security.py`
- `tests/ui/test_session_manager_public_api.py`

# Release Review Checklist

- Run focused tests for approval changes before release.
- Review affected files for the same control-flow shape before shipping.
