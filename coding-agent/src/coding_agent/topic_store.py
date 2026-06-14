"""Deprecated import location. Moved to coding_agent.topics.store."""
import sys as _sys

from coding_agent.topics import store as _moved

_sys.modules[__name__] = _moved
