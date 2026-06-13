"""Deprecated import location. Moved to coding_agent.topics.provenance."""
import sys as _sys

from coding_agent.topics import provenance as _moved

_sys.modules[__name__] = _moved
