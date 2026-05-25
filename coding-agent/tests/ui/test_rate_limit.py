import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import patch

import coding_agent.server.rate_limit as server_rate_limit
import coding_agent.ui.rate_limit as ui_rate_limit


def _load_ui_rate_limit_shim() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "coding_agent"
        / "ui"
        / "rate_limit.py"
    )
    spec = importlib.util.spec_from_file_location("test_rate_limit_module", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    fake_slowapi = SimpleNamespace(
        Limiter=lambda **kwargs: SimpleNamespace(**kwargs),
        util=SimpleNamespace(get_remote_address=lambda *args, **kwargs: "127.0.0.1"),
    )
    with patch.dict(
        sys.modules, {"slowapi": fake_slowapi, "slowapi.util": fake_slowapi.util}
    ):
        spec.loader.exec_module(module)
    return module


def test_ui_rate_limit_is_server_rate_limit_module():
    assert ui_rate_limit is server_rate_limit
    assert ui_rate_limit.limiter is server_rate_limit.limiter


def test_ui_rate_limit_file_shim_exports_server_rate_limit_api():
    rate_limit = _load_ui_rate_limit_shim()

    assert rate_limit._get_storage_uri is server_rate_limit._get_storage_uri
    assert rate_limit.RateLimits is server_rate_limit.RateLimits
    assert rate_limit.limiter is server_rate_limit.limiter


class TestRateLimitStorageUri:
    def test_default_is_memory(self):
        with patch.dict(os.environ, {}, clear=True):
            assert server_rate_limit._get_storage_uri() == "memory://"

    def test_redis_url_from_env(self):
        with patch.dict(
            os.environ, {"AGENT_SESSION_REDIS_URL": "redis://redis:6379/0"}
        ):
            assert server_rate_limit._get_storage_uri() == "redis://redis:6379/0"

    def test_empty_redis_url_falls_back_to_memory(self):
        with patch.dict(os.environ, {"AGENT_SESSION_REDIS_URL": ""}):
            assert server_rate_limit._get_storage_uri() == "memory://"
