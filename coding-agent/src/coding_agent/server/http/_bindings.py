"""Look up names on the stable http_server module.

Tests monkeypatch ``coding_agent.server.http_server`` attributes
(config loaders, cloud helpers, asyncio, EventSourceResponse,
list_provider_models, console store getters, and background tasks).
Implementation code must read those names from the stable module so
setattr reaches the call site.
"""

from __future__ import annotations

import sys
from types import ModuleType

_MODULE = "coding_agent.server.http_server"
LOGGER_NAME = "coding_agent.server.http_server"


def module() -> ModuleType:
    loaded = sys.modules.get(_MODULE)
    if loaded is None:
        raise RuntimeError(f"{_MODULE} is not loaded")
    return loaded
