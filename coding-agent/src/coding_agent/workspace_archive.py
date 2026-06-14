"""Deprecated import location. Moved to coding_agent.environment.archive."""
import sys as _sys

from coding_agent.environment import archive as _moved

_sys.modules[__name__] = _moved
