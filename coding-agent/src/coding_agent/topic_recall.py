"""Deprecated import location. Moved to coding_agent.topics.recall."""
import sys as _sys

from coding_agent.topics import recall as _moved

_sys.modules[__name__] = _moved
