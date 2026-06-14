"""Deprecated import location. Moved to coding_agent.stores.runtime_store."""
import sys as _sys

from coding_agent.stores import runtime_store as _moved

_sys.modules[__name__] = _moved
