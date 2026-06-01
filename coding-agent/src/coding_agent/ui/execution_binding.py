"""Compatibility alias for :mod:`coding_agent.environment.execution_binding`."""

from __future__ import annotations

import sys

from coding_agent.environment import execution_binding as _execution_binding

sys.modules[__name__] = _execution_binding
