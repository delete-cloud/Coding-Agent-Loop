"""Deprecated import location. Moved to coding_agent.observability.agent."""

import sys as _sys
from coding_agent.observability import agent as _moved

_sys.modules[__name__] = _moved
