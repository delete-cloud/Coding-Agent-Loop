"""Compatibility alias for :mod:`coding_agent.server.developer_console`."""

from __future__ import annotations

import sys

from coding_agent.server import developer_console as _developer_console

sys.modules[__name__] = _developer_console
