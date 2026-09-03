"""Configuration with layered precedence: CLI flags > env vars > defaults."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, SecretStr, field_validator

ProviderName = Literal[
    "openai",
    "anthropic",
    "copilot",
    "kimi",
    "kimi-code",
    "kimi-code-anthropic",
    "deepseek",
    "stepfun",
    "codex",
]

CODEX_ACCOUNT_LABEL_PATTERN = r"^[a-z0-9][a-z0-9-]{0,30}$"
_PROVIDER_NAME_VALUES = frozenset(get_args(ProviderName))


def validate_provider_value(value: str | None) -> str | None:
    """Allow known providers plus multi-account ``codex:<label>`` keys."""
    if value is None or value in _PROVIDER_NAME_VALUES:
        return value
    if value.startswith("codex:"):
        label = value.removeprefix("codex:")
        if re.fullmatch(CODEX_ACCOUNT_LABEL_PATTERN, label):
            return value
        raise ValueError(
            f"codex account label must match {CODEX_ACCOUNT_LABEL_PATTERN}: {value!r}"
        )
    message = f"provider must be one of {sorted(_PROVIDER_NAME_VALUES)} or 'codex:<label>', got {value!r}"
    raise ValueError(message)


class Config(BaseModel):
    """Validated agent configuration."""

    # Provider
    provider: str = "openai"
    model: str = "gpt-4o"
    api_key: SecretStr | None = None
    base_url: str | None = None

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, value: str) -> str:
        validated = validate_provider_value(value)
        if validated is None:
            raise ValueError("provider must not be null")
        return validated

    # Agent behavior
    max_steps: int = 30
    approval_mode: Literal["yolo", "interactive", "auto"] = "yolo"
    doom_threshold: int = 3

    # Paths
    repo: Path = Path(".")
    tape_dir: Path = Path.home() / ".coding-agent" / "tapes"

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


def load_config(cli_args: dict[str, object] | None = None) -> Config:
    """Load config with precedence: CLI flags > env vars > defaults."""
    values: dict[str, object] = {}

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

    if values.get("provider") == "anthropic" and not values.get("api_key"):
        anthropic_token = os.environ.get("ANTHROPIC_API_KEY")
        if anthropic_token:
            values["api_key"] = anthropic_token

    if values.get("provider") == "kimi" and "api_key" not in values:
        moonshot_token = os.environ.get("MOONSHOT_API_KEY")
        if moonshot_token:
            values["api_key"] = moonshot_token

    if (
        values.get("provider") in {"kimi-code", "kimi-code-anthropic"}
        and "api_key" not in values
    ):
        kimi_code_token = os.environ.get("KIMI_CODE_API_KEY")
        if kimi_code_token:
            values["api_key"] = kimi_code_token

    if values.get("provider") == "deepseek" and not values.get("api_key"):
        deepseek_token = os.environ.get("DEEPSEEK_API_KEY")
        if deepseek_token:
            values["api_key"] = deepseek_token

    if values.get("provider") == "stepfun" and not values.get("api_key"):
        stepfun_token = os.environ.get("STEP_API_KEY")
        if stepfun_token:
            values["api_key"] = stepfun_token

    return Config.model_validate(values)


# Default settings instance (can be overridden by load_config)
settings = load_config()
