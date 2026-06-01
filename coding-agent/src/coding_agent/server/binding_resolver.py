"""Compatibility alias for :mod:`coding_agent.environment.binding_resolver`."""

from __future__ import annotations

import sys

from coding_agent.environment import binding_resolver as _binding_resolver

sys.modules[__name__] = _binding_resolver
