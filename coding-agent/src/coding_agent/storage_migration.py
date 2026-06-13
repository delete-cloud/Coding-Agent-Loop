"""Deprecated import location. Moved to coding_agent.stores.migration."""
import sys as _sys
from coding_agent.stores import migration as _moved
_sys.modules[__name__] = _moved
