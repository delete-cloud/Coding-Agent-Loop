from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType


def _load_status_footer_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "coding_agent"
        / "ui"
        / "status_footer.py"
    )
    spec = importlib.util.spec_from_file_location(
        "test_status_footer_module", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STATUS_FOOTER_MODULE = _load_status_footer_module()
StatusFooter = STATUS_FOOTER_MODULE.StatusFooter


@dataclass
class FakeConsole:
    is_terminal: bool


def _make_footer(*, is_terminal: bool = True) -> StatusFooter:
    return StatusFooter(console=FakeConsole(is_terminal=is_terminal))


class TestSpikeVerdict:
    def test_compatible_environment_returns_persistent(self) -> None:
        footer = _make_footer(is_terminal=True)
        verdict = footer.run_spike_check()
        assert verdict == "persistent"
        assert footer.mode == "persistent"

    def test_nontty_returns_fallback(self) -> None:
        footer = _make_footer(is_terminal=False)
        verdict = footer.run_spike_check()
        assert verdict == "fallback-toolbar"
        assert footer.mode == "fallback-toolbar"


class TestEnableDisable:
    def test_enable_sets_enabled_flag(self) -> None:
        footer = _make_footer(is_terminal=True)
        footer.run_spike_check()
        footer.enable()
        assert footer.enabled is True
        assert footer.mode == "persistent"

    def test_disable_clears_enabled_flag(self) -> None:
        footer = _make_footer(is_terminal=True)
        footer.run_spike_check()
        footer.enable()
        footer.disable()
        assert footer.enabled is False

    def test_enable_nontty_is_noop(self) -> None:
        footer = _make_footer(is_terminal=False)
        footer.run_spike_check()
        footer.enable()
        assert footer.enabled is True
        assert footer.mode == "persistent"


class TestUpdate:
    def test_update_is_noop(self) -> None:
        footer = _make_footer(is_terminal=True)
        footer.run_spike_check()
        footer.update(model="gpt-4o", tokens_in=100, tokens_out=50, elapsed=5.0)
        assert footer.enabled is False

    def test_clear_and_redraw_is_noop(self) -> None:
        footer = _make_footer(is_terminal=True)
        footer.clear_and_redraw()
        assert footer.enabled is False
