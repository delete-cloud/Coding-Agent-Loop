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
- src/coding_agent/runs/serving_runtime.py
- src/coding_agent/server/session/approval.py
- src/coding_agent/server/session/manager.py
- tests/approval/test_store.py
- tests/integration/test_wire_http_integration.py
- tests/ui/test_http_server.py
- tests/ui/test_security.py
- tests/ui/test_session_manager_public_api.py
- tests/coding_agent/test_phase_f_serving.py
release_checks:
- Run focused tests for approval changes before release.
- Review affected files for the same control-flow shape before shipping.
---

# Summary

require store-backed requests across HTTP approval flow

# Trigger Conditions

- Changes in approval paths
- New-runtime HTTP decisions acknowledged before durable mailbox admission
- New-runtime approval waits routed through the legacy settled writer
- Process restart strands an admitted approval because resume only watches memory
- Timeout denial races a durable human decision under the same command identity
- Reconstructed approval waits reset the persisted per-run round budget
- Historical commit: `fix(approval): require store-backed requests across HTTP approval flow`

# Known Fix Signals

- `src/coding_agent/approval/store.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/schemas.py`
- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/runs/serving_runtime.py`
- `src/coding_agent/server/session/approval.py`
- `src/coding_agent/server/session/manager.py`
- `tests/approval/test_store.py`
- `tests/integration/test_wire_http_integration.py`
- `tests/ui/test_http_server.py`
- `tests/ui/test_security.py`
- `tests/ui/test_session_manager_public_api.py`
- `tests/coding_agent/test_phase_f_serving.py`

# Release Review Checklist

- Run focused tests for approval changes before release.
- Review affected files for the same control-flow shape before shipping.
- Confirm new-runtime HTTP approval admits the durable command before returning.
- Confirm the process-local coordinator is only a waiter wake mechanism.
- Confirm restart reconstructs `ApprovalResolved` from the durable mailbox.
- Confirm timeout and HTTP admission select one durable decision under a shared lock.
- Confirm reconstructed turns subtract the persisted `round_index` from `max_rounds`.
