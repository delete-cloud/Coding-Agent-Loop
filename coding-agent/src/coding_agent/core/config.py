"""Configuration with layered precedence: CLI flags > env vars > defaults."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, SecretStr


class Config(BaseModel):
    """Validated agent configuration."""

    # Provider
    provider: Literal["openai", "anthropic", "copilot"] = "openai"
    model: str = "gpt-4o"
    api_key: SecretStr | None = None
    base_url: str | None = None

    # Agent behavior
    max_steps: int = 30
    approval_mode: Literal["yolo", "interactive", "auto"] = "yolo"
    doom_threshold: int = 3

    # Paths
    repo: Path = Path(".")
    tape_dir: Path = Path.home() / ".coding-agent" / "tapes"
    skills_dir: Path = Path.home() / ".coding-agent" / "skills"

    # Sub-agents
    max_subagent_depth: int = 3
    subagent_max_steps: int = 15

    # Execution
    enable_parallel_tools: bool = True
    max_parallel_tools: int = 5

    # Caching
    enable_cache: bool = True
    cache_size: int = 100

    # HTTP Server settings
    http_api_key: str | None = None  # API key for HTTP API authentication


# Env var prefix → Config field mapping
_ENV_MAP: dict[str, str] = {
    "AGENT_API_KEY": "api_key",
    "AGENT_MODEL": "model",
    "AGENT_BASE_URL": "base_url",
    "AGENT_PROVIDER": "provider",
    "AGENT_MAX_STEPS": "max_steps",
    "AGENT_APPROVAL_MODE": "approval_mode",
    "AGENT_DOOM_THRESHOLD": "doom_threshold",
    "AGENT_REPO": "repo",
    "AGENT_ENABLE_PARALLEL_TOOLS": "enable_parallel_tools",
    "AGENT_MAX_PARALLEL_TOOLS": "max_parallel_tools",
    "AGENT_HTTP_API_KEY": "http_api_key",
}


def load_config(cli_args: dict | None = None) -> Config:
    """Load config with precedence: CLI flags > env vars > defaults."""
    values: dict = {}

    # Layer 1: env vars
    for env_key, field_name in _ENV_MAP.items():
        val = os.environ.get(env_key)
        if val is not None:
            values[field_name] = val

    # Layer 2: CLI args override env
    if cli_args:
        for k, v in cli_args.items():
            if v is not None:
                values[k] = v

    if values.get("provider") == "copilot" and "api_key" not in values:
        github_token = os.environ.get("GITHUB_TOKEN")
        if github_token:
            values["api_key"] = github_token

    return Config(**values)


# Default settings instance (can be overridden by load_config)
settings = load_config()
