"""PluginRegistry — manages plugin registration and hook lookup."""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable

from agentkit.errors import PluginError
from agentkit.plugin.protocol import Plugin


class PluginCapability(StrEnum):
    """Capability classes available to persistence-free plugins."""

    PENDING_FACT = "pending_fact"
    EFFECT_PLAN = "effect_plan"
    OBSERVER = "observer"


_CAPABILITY_HOOKS: dict[PluginCapability, frozenset[str]] = {
    PluginCapability.PENDING_FACT: frozenset({"build_context", "on_turn_end"}),
    PluginCapability.EFFECT_PLAN: frozenset({"get_tools", "get_proxy_tools"}),
    PluginCapability.OBSERVER: frozenset(
        {"on_error", "on_shutdown", "on_checkpoint", "on_session_event"}
    ),
}


@dataclass(frozen=True, slots=True)
class HookBinding:
    """A hook bound to its owning plugin and declared capabilities."""

    plugin_id: str
    hook: Callable[..., Any]
    capabilities: frozenset[PluginCapability] | None


class PluginRegistry:
    """Registry for agentkit plugins.

    Maintains insertion order. Provides hook lookup by name.
    """

    def __init__(self, specs: Mapping[str, object] | None = None) -> None:
        self._plugins: dict[str, Plugin] = {}
        self._hook_index: dict[str, list[HookBinding]] = {}
        self._specs: Mapping[str, object] | None = specs

    def register(self, plugin: Plugin) -> None:
        """Register a plugin. Raises PluginError on protocol or capability violations."""
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

        hooks = plugin.hooks()
        self._validate_capabilities(plugin, hooks)
        self._plugins[key] = plugin
        capabilities = getattr(plugin, "capabilities", None)
        for hook_name, hook_fn in hooks.items():
            self._hook_index.setdefault(hook_name, []).append(
                HookBinding(
                    plugin_id=key,
                    hook=hook_fn,
                    capabilities=capabilities,
                )
            )
            if self._specs is not None and hook_name not in self._specs:
                warnings.warn(
                    f"Plugin '{plugin.state_key}' registered unknown hook '{hook_name}' "
                    f"(not in HookSpec registry)",
                    UserWarning,
                    stacklevel=2,
                )

    @staticmethod
    def _validate_capabilities(
        plugin: Plugin, hooks: Mapping[str, Callable[..., Any]]
    ) -> None:
        declared = getattr(plugin, "capabilities", None)
        if declared is None:
            return
        if not isinstance(declared, frozenset) or not declared:
            raise PluginError(
                "capabilities must be a non-empty frozenset of PluginCapability values",
                plugin_id=plugin.state_key,
            )
        if any(not isinstance(capability, PluginCapability) for capability in declared):
            raise PluginError(
                "capabilities must contain only PluginCapability values",
                plugin_id=plugin.state_key,
            )

        allowed_hooks = frozenset().union(
            *(_CAPABILITY_HOOKS[capability] for capability in declared)
        )
        forbidden_hooks = sorted(set(hooks) - allowed_hooks)
        if forbidden_hooks:
            raise PluginError(
                f"capability-declared plugin '{plugin.state_key}' cannot register hooks: "
                f"{', '.join(forbidden_hooks)}",
                plugin_id=plugin.state_key,
            )

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
        return [binding.hook for binding in self._hook_index.get(hook_name, ())]

    def get_hook_bindings(self, hook_name: str) -> tuple[HookBinding, ...]:
        """Return immutable hook ownership metadata in registration order."""
        return tuple(self._hook_index.get(hook_name, ()))
