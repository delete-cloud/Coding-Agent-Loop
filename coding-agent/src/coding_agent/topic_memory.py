"""Deprecated import location. Moved to coding_agent.topics.memory."""
import sys as _sys

from coding_agent.topics import memory as _moved

_sys.modules[__name__] = _moved
