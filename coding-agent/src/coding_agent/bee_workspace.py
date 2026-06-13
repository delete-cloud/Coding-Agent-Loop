"""Deprecated import location. Moved to coding_agent.bee.workspace."""

import sys as _sys
from coding_agent.bee import workspace as _moved

_sys.modules[__name__] = _moved
