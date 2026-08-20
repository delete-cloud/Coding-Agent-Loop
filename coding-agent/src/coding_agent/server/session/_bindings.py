"""Look up names on the stable session_manager module.

Tests monkeypatch ``coding_agent.server.session_manager`` attributes
(PipelineAdapter, create_session_store, PGRuntimeStore, importlib, asyncio)
and historically also ``SessionManager.<method>.__globals__['PipelineAdapter']``.
"""

from __future__ import annotations

import sys
from types import ModuleType

from coding_agent.adapter import PipelineAdapter as _ORIGINAL_PIPELINE_ADAPTER

_MODULE = "coding_agent.server.session_manager"
LOGGER_NAME = "coding_agent.server.session_manager"


def module() -> ModuleType:
    loaded = sys.modules.get(_MODULE)
    if loaded is None:
        raise RuntimeError(f"{_MODULE} is not loaded")
    return loaded


def pipeline_adapter() -> type:
    loaded = module()
    for attr in ("run_agent", "ensure_session_runtime"):
        method = getattr(loaded.SessionManager, attr, None)
        if method is None:
            continue
        patched = method.__globals__.get("PipelineAdapter")
        if patched is not None and patched is not _ORIGINAL_PIPELINE_ADAPTER:
            return patched
    return loaded.PipelineAdapter
