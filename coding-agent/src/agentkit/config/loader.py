"""TOML configuration loader for agentkit agents."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentkit.errors import ConfigError


@dataclass
class AgentConfig:
    """Parsed agent configuration from TOML."""

    name: str
    model: str
    provider: str
    system_prompt: str = ""
    plugins: list[str] = field(default_factory=list)
    max_turns: int = 30
    extra: dict[str, Any] = field(default_factory=dict)


def load_config(path: Path) -> AgentConfig:
    """Load and validate an agent.toml configuration file."""
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"failed to parse {path}: {exc}") from exc

    if "agent" not in data:
        raise ConfigError(f"missing [agent] section in {path}")

    agent = data["agent"]

    for required in ("name", "model", "provider"):
        if required not in agent:
            raise ConfigError(f"missing required field '{required}' in [agent] section")

    plugins_section = agent.get("plugins", {})
    plugins = (
        plugins_section.get("enabled", []) if isinstance(plugins_section, dict) else []
    )

    extra = {k: v for k, v in data.items() if k != "agent"}

    return AgentConfig(
        name=agent["name"],
        model=agent["model"],
        provider=agent["provider"],
        system_prompt=agent.get("system_prompt", ""),
        plugins=plugins,
        max_turns=agent.get("max_turns", 30),
        extra=extra,
    )
