"""Deprecated import location. Moved to coding_agent.bee.launch."""

import sys as _sys
from coding_agent.bee import launch as _moved

_sys.modules[__name__] = _moved
