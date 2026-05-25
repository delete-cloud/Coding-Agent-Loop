"""Compatibility alias for :mod:`coding_agent.server.rate_limit`."""

from __future__ import annotations

import importlib
import sys

_module_name = __name__
_rate_limit = importlib.import_module("coding_agent.server.rate_limit")
globals().update(_rate_limit.__dict__)
sys.modules[_module_name] = _rate_limit
_parent_name, _, _child_name = _module_name.rpartition(".")
if _parent_name:
    setattr(sys.modules[_parent_name], _child_name, _rate_limit)
