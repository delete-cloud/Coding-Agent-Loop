"""Compatibility alias for :mod:`coding_agent.server.execution_binding`."""

from __future__ import annotations

import sys

from coding_agent.server import execution_binding as _execution_binding

sys.modules[__name__] = _execution_binding
