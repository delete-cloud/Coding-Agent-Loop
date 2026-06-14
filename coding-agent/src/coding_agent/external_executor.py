"""Deprecated import location. Moved to coding_agent.executors.external."""
import sys as _sys
from coding_agent.executors import external as _moved
_sys.modules[__name__] = _moved
