"""HookRuntime — the core dispatch engine for plugin hooks."""

from __future__ import annotations

import logging
from typing import Any

from agentkit.errors import HookError
from agentkit.plugin.registry import PluginRegistry

logger = logging.getLogger(__name__)


class HookRuntime:
    """Dispatches hook calls to registered plugin callables.

    Args:
        registry: The PluginRegistry containing all registered plugins.
    """

    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry

    def call_first(self, hook_name: str, **kwargs: Any) -> Any:
        """Call hooks in order, return the first non-None result.

        Returns None if no hooks are registered or all return None.
        Raises HookError if a hook raises an exception.
        """
        callables = self._registry.get_hooks(hook_name)
        for fn in callables:
            try:
                result = fn(**kwargs)
                if result is not None:
                    return result
            except Exception as exc:
                raise HookError(
                    str(exc),
                    hook_name=hook_name,
                ) from exc
        return None

    def call_many(self, hook_name: str, **kwargs: Any) -> list[Any]:
        """Call all hooks, collect non-None results.

        Returns empty list if no hooks registered.
        Raises HookError if any hook raises an exception.
        """
        callables = self._registry.get_hooks(hook_name)
        results: list[Any] = []
        for fn in callables:
            try:
                result = fn(**kwargs)
                if result is not None:
                    results.append(result)
            except Exception as exc:
                raise HookError(
                    str(exc),
                    hook_name=hook_name,
                ) from exc
        return results

    def notify(self, hook_name: str, **kwargs: Any) -> None:
        """Fire-and-forget: call all hooks, swallow exceptions.

        Used for observer hooks (on_error, on_checkpoint) where failures
        should not interrupt the main pipeline.
        """
        callables = self._registry.get_hooks(hook_name)
        for fn in callables:
            try:
                fn(**kwargs)
            except Exception:
                logger.exception("Observer hook '%s' raised (swallowed)", hook_name)
