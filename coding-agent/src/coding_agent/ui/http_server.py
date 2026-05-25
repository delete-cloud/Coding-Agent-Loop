"""Compatibility alias for :mod:`coding_agent.server.http_server`."""

from __future__ import annotations

import importlib
import sys

_module_name = __name__
_http_server = importlib.import_module("coding_agent.server.http_server")
globals().update(_http_server.__dict__)
sys.modules[_module_name] = _http_server
_parent_name, _, _child_name = _module_name.rpartition(".")
if _parent_name:
    setattr(sys.modules[_parent_name], _child_name, _http_server)
