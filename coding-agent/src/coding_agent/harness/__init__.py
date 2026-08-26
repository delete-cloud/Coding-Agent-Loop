"""Harness control-plane P3 contract surface (ADR-0076).

This package exposes contract constants only. The OpenRPC document and the
JSON Schema Draft 7 files under ``protocol/harness/`` freeze the eleven D14
verbs with zero handlers; runtime wiring, codegen, and the unix socket land
in later cuts (P4 owns listen, singleton daemon, and the instance lease).
"""

from __future__ import annotations

from pathlib import Path

UNIX_SOCKET_STATUS = "unavailable"
"""P3 marks the harness unix socket unavailable; P4 owns listen/singleton/lease."""


def unix_socket_status() -> str:
    """Return the harness unix-socket availability marker.

    P3 never binds a unix-domain socket, never adds a ``--socket`` CLI flag,
    and never listens, so this is always ``"unavailable"`` until P4.
    """
    return UNIX_SOCKET_STATUS


def protocol_dir() -> Path:
    """Return the repo-local harness protocol directory (openrpc + schemas)."""
    return Path(__file__).resolve().parents[3] / "protocol" / "harness"
