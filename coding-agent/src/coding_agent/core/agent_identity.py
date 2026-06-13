"""Single source of truth for resolving the active agent_id of a pipeline run.

Three places historically held an ``agent_id``:

* ``PipelineContext.run_context.agent_id`` (canonical, ``str | None``),
* ``PipelineContext.config["agent_id"]`` (legacy, always ``str``),
* ``PipelineAdapter.agent_id`` (constructor argument, always ``str``).

This module centralizes resolution so consumers (subagent dispatch, metrics,
storage, etc.) never read the legacy slot directly when a ``run_context`` is
available.
"""

from __future__ import annotations

from typing import Any


def effective_agent_id(ctx: Any) -> str | None:
    """Return the canonical ``agent_id`` for ``ctx``.

    Prefers ``ctx.run_context.agent_id`` (``str | None``); falls back to
    ``ctx.config["agent_id"]`` for legacy callers that do not populate a
    ``run_context``. ``None`` and ``""`` from either source both mean
    "root agent" and are normalized to ``None``.

    Accepts duck-typed ``ctx`` objects (e.g. test fakes) that may lack a
    ``run_context`` attribute or a ``config`` mapping; both cases degrade
    to ``None``.
    """
    run_context = getattr(ctx, "run_context", None)
    if run_context is not None:
        return run_context.agent_id
    config = getattr(ctx, "config", None) or {}
    raw = config.get("agent_id") if hasattr(config, "get") else None
    if raw is None or raw == "":
        return None
    return str(raw)


def legacy_agent_id_str(agent_id: str | None) -> str:
    """Convert a canonical ``agent_id`` to the legacy non-``None`` string form."""
    return "" if agent_id is None else agent_id
