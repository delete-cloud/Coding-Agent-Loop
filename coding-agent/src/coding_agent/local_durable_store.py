"""Deprecated import location. Moved to coding_agent.stores.durable_local."""
import sys as _sys
from coding_agent.stores import durable_local as _moved
_sys.modules[__name__] = _moved
