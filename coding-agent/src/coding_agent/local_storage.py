"""Deprecated import location. Moved to coding_agent.stores.local."""
import sys as _sys

from coding_agent.stores import local as _moved

_sys.modules[__name__] = _moved
