"""Compatibility alias for :mod:`coding_agent.server.stores.session_store`."""

from __future__ import annotations

import sys

from coding_agent.server.stores import session_store as _session_store

sys.modules[__name__] = _session_store
