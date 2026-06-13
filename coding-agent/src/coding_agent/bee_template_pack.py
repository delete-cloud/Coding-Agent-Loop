"""Deprecated import location. Moved to coding_agent.bee.template_pack."""

import sys as _sys
from coding_agent.bee import template_pack as _moved

_sys.modules[__name__] = _moved
