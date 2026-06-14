"""Deprecated import location. Moved to coding_agent.observability.metrics."""

import sys as _sys
from coding_agent.observability import metrics as _moved

_sys.modules[__name__] = _moved
