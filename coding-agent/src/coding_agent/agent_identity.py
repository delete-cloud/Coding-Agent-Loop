"""Deprecated import location. Moved to coding_agent.core.agent_identity."""

import sys as _sys
from coding_agent.core import agent_identity as _moved

_sys.modules[__name__] = _moved
