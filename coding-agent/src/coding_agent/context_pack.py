"""Deprecated import location. Moved to coding_agent.topics.context_pack."""
import sys as _sys

from coding_agent.topics import context_pack as _moved

_sys.modules[__name__] = _moved
