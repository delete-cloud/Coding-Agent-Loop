from __future__ import annotations

from pathlib import Path

import pytest

from agentkit.runtime import EffectSettled
from tests.coding_agent.test_durable_commit_ports import (
    SESSION_STATE,
    STAMP,
    _authorized_replay_port,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_root_startup_recovers_dispatched_attempt_without_process_marker(
    tmp_path: Path,
    store_kind: str,
) -> None:
    store, port_type, _port, runner_request = await _authorized_replay_port(
        tmp_path,
        store_kind,
    )
    restarted = port_type(
        store,
        session_state=SESSION_STATE,
        clock=lambda: STAMP,
    )
    recovery = await restarted.recover_authorization_without_marker(runner_request)
    assert recovery is not None
    assert isinstance(recovery.step_input, EffectSettled)
