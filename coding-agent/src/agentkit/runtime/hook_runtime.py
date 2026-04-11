"""HookRuntime — the core dispatch engine for plugin hooks."""

from __future__ import annotations

import logging
from typing import Any

from agentkit.errors import HookError, HookTypeError
from agentkit.plugin.registry import PluginRegistry
from agentkit.runtime.hookspecs import HookSpec

logger = logging.getLogger(__name__)


class HookRuntime:
    """Dispatches hook calls to registered plugin callables.

    Args:
        registry: The PluginRegistry containing all registered plugins.
        specs: Optional HookSpec mapping for return type validation.
               If None, no type validation is performed (backward compat).
    """

    def __init__(
        self,
        registry: PluginRegistry,
        specs: dict[str, HookSpec] | None = None,
    ) -> None:
        self._registry = registry
        self._specs = specs or {}

    def call_first(self, hook_name: str, **kwargs: Any) -> Any:
        """Call hooks in order, return the first non-None result.

        Returns None if no hooks are registered or all return None.
        Raises HookError if a hook raises an exception.
        Raises HookTypeError if result does not match declared return_type.
        """
        callables = self._registry.get_hooks(hook_name)
        for fn in callables:
            try:
                result = fn(**kwargs)
                if result is not None:
                    self._validate_return(hook_name, result)
                    return result
            except (HookError, HookTypeError):
                raise
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
        Raises HookTypeError if any result does not match declared return_type.
        """
        callables = self._registry.get_hooks(hook_name)
        results: list[Any] = []
        for fn in callables:
            try:
                result = fn(**kwargs)
                if result is not None:
                    self._validate_return(hook_name, result)
                    results.append(result)
            except (HookError, HookTypeError):
                raise
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

    def _validate_return(self, hook_name: str, result: Any) -> None:
        spec = self._specs.get(hook_name)
        if spec is None or spec.return_type is None:
            return
        if not isinstance(result, spec.return_type):
            raise HookTypeError(
                f"Hook '{hook_name}' declared return_type={spec.return_type.__name__}, "
                f"got {type(result).__name__}: {repr(result)[:100]}",
                hook_name=hook_name,
            )
