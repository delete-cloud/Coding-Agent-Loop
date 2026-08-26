from __future__ import annotations

import logging
from pathlib import Path

import pytest

from coding_agent.server.http import config as server_config


def test_documented_serve_defaults_load_package_agent_config_without_tracebacks(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("CODING_AGENT_SERVER_CONFIG", raising=False)
    expected_path = Path(server_config.__file__).resolve().parents[2] / "agent.toml"

    with caplog.at_level(logging.WARNING):
        storage = server_config._load_storage_config()
        runtime_defaults = server_config._load_agent_runtime_defaults()
        http = server_config._load_server_config()

    assert server_config._server_config_path() == expected_path
    assert storage == {
        "tape_backend": "sqlite",
        "http_session_backend": "sqlite",
        "checkpoint_backend": "sqlite",
        "runtime_backend": "sqlite",
        "doc_backend": "lancedb",
        "paths": {
            "local": "./data/local.sqlite3",
            "docs": "./data/docs",
        },
    }
    assert runtime_defaults == {
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "max_steps": 30,
    }
    assert http == {}
    assert "config file not found" not in caplog.text
    assert not [record for record in caplog.records if record.exc_info is not None]
