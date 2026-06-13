"""Deprecated import location. Moved to coding_agent.bee.command_bridge."""

import sys as _sys
from coding_agent.bee import command_bridge as _moved

_sys.modules[__name__] = _moved
