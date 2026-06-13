"""Deprecated import location. Moved to coding_agent.bee.runtime."""

import sys as _sys
from coding_agent.bee import runtime as _moved

_sys.modules[__name__] = _moved
