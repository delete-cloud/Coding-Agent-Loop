from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from agentkit.runtime import SegmentCoordinator
from coding_agent.runtime_activation import RUNTIME_VERSION_NEW
from coding_agent.runs.serving_runtime import (
    DurableSegmentTurnAdapter,
    build_new_runtime_turn_adapter,
    session_serving_turn_kind,
)
from coding_agent.runs.turn_execution import DurableSegmentRunner
from coding_agent.server.stores.session_owner_store import OwnerAuthority
from coding_agent.stores.rtstore.harness import effect_status_may_replace
from tests.coding_agent.test_harness_p2_fact_source import (
    OWNER_ID,
    SESSION_ID,
    _open_store,
)


@dataclass
class _Session:
    runtime_version: str


class _Probe:
    def observe(self) -> object:
        return object()

    async def wait(self, after: object) -> object:
        del after
        return self.observe()


class _Sink:
    async def emit(self, _item: object) -> None:
        return None


@dataclass
class _Outcome:
    final_message: str


class _Coordinator:
    async def run(
        self,
        request: object,
        control_probe: object,
        frame_sink: object,
        committed_fact_sink: object,
    ) -> _Outcome:
        del request, control_probe, frame_sink, committed_fact_sink
        return _Outcome(final_message="ok")


class _Port:
    def consume_authorization_replay_marker(self, request: object) -> None:
        del request
        return None

    async def recover_authorization_without_marker(self, request: object) -> None:
        del request
        return None


def test_session_serving_turn_kind_uses_session_version() -> None:
    assert session_serving_turn_kind(_Session(RUNTIME_VERSION_NEW)) == (
        "durable_segment_runner"
    )
    assert session_serving_turn_kind(_Session("legacy")) == "pipeline_adapter"


def test_generic_rank_does_not_treat_settled_as_completed() -> None:
    assert effect_status_may_replace(current="prepared", incoming="settled") is False
    assert effect_status_may_replace(current="settled", incoming="prepared") is False
    assert effect_status_may_replace(current="prepared", incoming="completed") is True


@pytest.mark.asyncio
async def test_durable_segment_turn_adapter_does_not_use_pipeline() -> None:
    adapter = DurableSegmentTurnAdapter(
        runner=DurableSegmentRunner(coordinator=_Coordinator(), commit_port=_Port()),
        request_for_prompt=lambda prompt: prompt,
        control_probe=_Probe(),
        frame_sink=_Sink(),
        committed_fact_sink=_Sink(),
    )
    outcome = await adapter.run_turn("hello")
    assert isinstance(outcome, _Outcome)
    assert outcome.final_message == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_serving_factory_builds_coordinator_and_commit_port(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    adapter = build_new_runtime_turn_adapter(
        session=_Session(RUNTIME_VERSION_NEW),
        run_id="run-serving",
        authority=owner,
        store=store,
        state_version=None,
    )
    assert isinstance(adapter, DurableSegmentTurnAdapter)
    assert isinstance(adapter.runner.coordinator, SegmentCoordinator)
    assert adapter.runner.commit_port is not None
    assert owner.session_id == SESSION_ID
    assert owner.owner_id == OWNER_ID
    assert isinstance(owner, OwnerAuthority)
