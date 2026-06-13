"""Deprecated import location. Moved to coding_agent.adapter.types."""

import sys as _sys
from coding_agent.adapter import types as _moved

_sys.modules[__name__] = _moved
