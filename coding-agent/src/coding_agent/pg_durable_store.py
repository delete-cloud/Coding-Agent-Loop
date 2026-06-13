"""Deprecated import location. Moved to coding_agent.stores.durable_pg."""
import sys as _sys
from coding_agent.stores import durable_pg as _moved
_sys.modules[__name__] = _moved
