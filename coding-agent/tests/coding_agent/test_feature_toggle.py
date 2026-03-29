"""Tests for USE_PIPELINE feature toggle."""

import os
import pytest


def test_use_pipeline_respects_env_var_true(monkeypatch):
    """Test that use_pipeline() returns True when USE_PIPELINE=1."""
    monkeypatch.setenv("USE_PIPELINE", "1")
    from coding_agent.core.config import use_pipeline

    assert use_pipeline() is True


def test_use_pipeline_respects_env_var_false(monkeypatch):
    """Test that use_pipeline() returns False when USE_PIPELINE not set."""
    monkeypatch.delenv("USE_PIPELINE", raising=False)
    from coding_agent.core.config import use_pipeline

    assert use_pipeline() is False


def test_use_pipeline_respects_env_var_true_variant(monkeypatch):
    """Test that use_pipeline() accepts 'true' and 'yes' variants."""
    from coding_agent.core.config import use_pipeline

    for value in ["true", "yes", "1"]:
        monkeypatch.setenv("USE_PIPELINE", value)
        # Force reimport to pick up new env var
        import importlib
        import coding_agent.core.config

        importlib.reload(coding_agent.core.config)
        from coding_agent.core.config import use_pipeline as use_pipeline_reloaded

        assert use_pipeline_reloaded() is True, f"Failed for USE_PIPELINE={value}"


def test_use_pipeline_override_true(monkeypatch):
    """Test that override=True returns True regardless of env var."""
    monkeypatch.delenv("USE_PIPELINE", raising=False)
    from coding_agent.core.config import use_pipeline

    assert use_pipeline(override=True) is True


def test_use_pipeline_override_false(monkeypatch):
    """Test that override=False returns False regardless of env var."""
    monkeypatch.setenv("USE_PIPELINE", "1")
    from coding_agent.core.config import use_pipeline

    assert use_pipeline(override=False) is False


def test_use_pipeline_override_none_respects_env(monkeypatch):
    """Test that override=None defaults to env var behavior."""
    monkeypatch.setenv("USE_PIPELINE", "1")
    from coding_agent.core.config import use_pipeline

    assert use_pipeline(override=None) is True


def test_use_pipeline_whitespace_handling(monkeypatch):
    """Test that use_pipeline handles whitespace in env var."""
    monkeypatch.setenv("USE_PIPELINE", " 1 ")
    from coding_agent.core.config import use_pipeline

    assert use_pipeline() is True
