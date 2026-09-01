from __future__ import annotations

from pathlib import Path

import pytest

from tests.coding_agent.test_harness_p2_fact_source import _open_store


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_activation_flag_defaults_off(store_kind: str, tmp_path: Path) -> None:
    store, _owner = await _open_store(store_kind, tmp_path)
    activation = await store.load_runtime_activation()
    assert activation.new_sessions_enabled is False
