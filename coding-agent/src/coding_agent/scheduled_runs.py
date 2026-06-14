"""Deprecated import location. Moved to coding_agent.runs.scheduled."""
import sys as _sys
from coding_agent.runs import scheduled as _moved
_sys.modules[__name__] = _moved
