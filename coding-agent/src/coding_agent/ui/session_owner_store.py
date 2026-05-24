"""Compatibility alias for :mod:`coding_agent.server.stores.session_owner_store`."""

from __future__ import annotations

import sys

from coding_agent.server.stores import session_owner_store as _session_owner_store

sys.modules[__name__] = _session_owner_store
