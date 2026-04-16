from __future__ import annotations

import asyncio

import pytest

from coding_agent.subagents.coordinator import ChildWorkerCoordinator


@pytest.mark.asyncio
async def test_allocate_child_id_is_unique_per_parent_turn() -> None:
    coordinator = ChildWorkerCoordinator()

    child_1 = coordinator.allocate_child_id(parent_agent_id="")
    child_2 = coordinator.allocate_child_id(parent_agent_id="")

    assert child_1 == "child-1"
    assert child_2 == "child-2"


@pytest.mark.asyncio
async def test_write_lease_allows_only_one_holder_at_a_time() -> None:
    coordinator = ChildWorkerCoordinator()
    events: list[str] = []

    async def worker(name: str) -> None:
        async with coordinator.acquire_write_lease():
            events.append(f"{name}:start")
            await asyncio.sleep(0.01)
            events.append(f"{name}:end")

    await asyncio.gather(worker("a"), worker("b"))

    assert events in (
        ["a:start", "a:end", "b:start", "b:end"],
        ["b:start", "b:end", "a:start", "a:end"],
    )
