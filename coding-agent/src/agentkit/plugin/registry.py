"""PluginRegistry — manages plugin registration and hook lookup."""

from __future__ import annotations

from typing import Any, Callable

from agentkit.errors import PluginError
from agentkit.plugin.protocol import Plugin


class PluginRegistry:
    """Registry for agentkit plugins.

    Maintains insertion order. Provides hook lookup by name.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}
        self._hook_index: dict[str, list[Callable[..., Any]]] = {}

    def register(self, plugin: Plugin) -> None:
        """Register a plugin. Raises PluginError on protocol violation or duplicate key."""
        if not isinstance(plugin, Plugin):
            raise PluginError(
                f"{type(plugin).__name__} does not satisfy Plugin protocol",
                plugin_id=getattr(plugin, "state_key", "<unknown>"),
            )
        key = plugin.state_key
        if key in self._plugins:
            raise PluginError(
                f"duplicate state_key '{key}'",
                plugin_id=key,
            )
        self._plugins[key] = plugin
        for hook_name, hook_fn in plugin.hooks().items():
            self._hook_index.setdefault(hook_name, []).append(hook_fn)

    def plugin_ids(self) -> list[str]:
        """Return all registered plugin IDs in insertion order."""
        return list(self._plugins.keys())

    def get(self, plugin_id: str) -> Plugin:
        """Get a plugin by state_key. Raises PluginError if not found."""
        if plugin_id not in self._plugins:
            raise PluginError(
                f"plugin '{plugin_id}' not found",
                plugin_id=plugin_id,
            )
        return self._plugins[plugin_id]

    def get_hooks(self, hook_name: str) -> list[Callable[..., Any]]:
        """Return all callables registered for a hook name."""
        return list(self._hook_index.get(hook_name, []))
