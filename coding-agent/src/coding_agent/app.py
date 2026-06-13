"""Deprecated import location. Moved to coding_agent.core.app."""

import sys as _sys
from coding_agent.core import app as _moved

_sys.modules[__name__] = _moved
