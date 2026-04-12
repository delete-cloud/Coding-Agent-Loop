import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import patch


def _load_rate_limit_module() -> ModuleType:
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


class TestRateLimitStorageUri:
    def test_default_is_memory(self):
        with patch.dict(os.environ, {}, clear=True):
            rate_limit = _load_rate_limit_module()

            assert rate_limit._get_storage_uri() == "memory://"

    def test_redis_url_from_env(self):
        with patch.dict(
            os.environ, {"AGENT_SESSION_REDIS_URL": "redis://redis:6379/0"}
        ):
            rate_limit = _load_rate_limit_module()

            assert rate_limit._get_storage_uri() == "redis://redis:6379/0"

    def test_empty_redis_url_falls_back_to_memory(self):
        with patch.dict(os.environ, {"AGENT_SESSION_REDIS_URL": ""}):
            rate_limit = _load_rate_limit_module()

            assert rate_limit._get_storage_uri() == "memory://"
