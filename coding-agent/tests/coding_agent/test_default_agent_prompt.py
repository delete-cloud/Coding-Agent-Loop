from __future__ import annotations

from pathlib import Path

from agentkit.config.loader import load_config


def test_default_system_prompt_respects_explicit_output_constraints() -> None:
    config_path = Path(__file__).parents[2] / "src" / "coding_agent" / "agent.toml"

    config = load_config(config_path)

    assert "Always explain your reasoning" not in config.system_prompt
    assert (
        "Unless the user explicitly requests concise, answer-only, or "
        "machine-readable output"
    ) in config.system_prompt
    assert "follow it exactly" in config.system_prompt
