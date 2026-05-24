"""Compatibility alias for :mod:`coding_agent.server.session_manager`."""

from __future__ import annotations

import sys

from coding_agent.server import session_manager as _session_manager

sys.modules[__name__] = _session_manager
