"""Compatibility alias for :mod:`coding_agent.server.http_server`."""

from __future__ import annotations

import importlib
import sys

_SERVER_MODULE = "coding_agent.server.http_server"
_previous_module = sys.modules.pop(_SERVER_MODULE, None)
try:
    _http_server = importlib.import_module(_SERVER_MODULE)
except Exception:
    if _previous_module is not None:
        sys.modules[_SERVER_MODULE] = _previous_module
    raise

sys.modules[__name__] = _http_server
