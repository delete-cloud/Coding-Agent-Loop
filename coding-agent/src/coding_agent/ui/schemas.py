"""Compatibility exports for :mod:`coding_agent.server.schemas`."""

from __future__ import annotations

from coding_agent.server import schemas as _schemas

for _name, _value in _schemas.__dict__.items():
    if _name not in {
        "__builtins__",
        "__cached__",
        "__file__",
        "__loader__",
        "__name__",
        "__package__",
        "__spec__",
    }:
        globals()[_name] = _value
