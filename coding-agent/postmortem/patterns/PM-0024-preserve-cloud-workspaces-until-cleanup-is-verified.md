---
id: PM-0024
title: Preserve cloud workspaces until cleanup is verified
status: active
severity: medium
confidence: medium
subsystems:
- environment
- http
related_commits: []
related_files:
- src/coding_agent/environment/docker_workspace_provider.py
- src/coding_agent/ui/http_server.py
- tests/coding_agent/environment/test_docker_workspace_provider.py
- tests/ui/test_http_server.py
release_checks:
- Run focused cloud workspace rollback regressions in `tests/coding_agent/environment/test_docker_workspace_provider.py` and `tests/ui/test_http_server.py` before release.
- Review create-then-cleanup, cancellation, and external-subprocess verification paths before deleting host workspace state.
---

# Summary

Cloud workspace teardown must preserve host workspace state until container cleanup is known to have succeeded or the container is known to be absent.

# Trigger Conditions

- Changes in cloud workspace provisioning, rollback, Docker cleanup, HTTP session creation, or cancellation handling.
- Review feedback mentions leaked provisioned workspaces, skipped rollback on cancellation, or treating Docker inspect failures as missing containers.

# Known Fix Signals

- `src/coding_agent/environment/docker_workspace_provider.py`
- `src/coding_agent/ui/http_server.py`
- `tests/coding_agent/environment/test_docker_workspace_provider.py`
- `tests/ui/test_http_server.py`

# Release Review Checklist

- Run focused cloud workspace rollback regressions in `tests/coding_agent/environment/test_docker_workspace_provider.py` and `tests/ui/test_http_server.py` before release.
- Review create-then-cleanup, cancellation, and external-subprocess verification paths before deleting host workspace state.
