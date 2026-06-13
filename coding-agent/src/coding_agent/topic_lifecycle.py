"""Deprecated import location. Moved to coding_agent.topics.lifecycle."""
import sys as _sys

from coding_agent.topics import lifecycle as _moved

_sys.modules[__name__] = _moved
