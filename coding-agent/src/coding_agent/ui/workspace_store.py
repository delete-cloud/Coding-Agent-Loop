"""Compatibility alias for :mod:`coding_agent.server.stores.workspace_store`."""

from __future__ import annotations

import sys

from coding_agent.server.stores import workspace_store as _workspace_store

sys.modules[__name__] = _workspace_store
