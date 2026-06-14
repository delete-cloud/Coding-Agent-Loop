from __future__ import annotations

from dataclasses import dataclass

from coding_agent.stores.runtime_store import RuntimeEventRecord
from coding_agent.stores import RuntimeEventStore

from .display import DisplayEvent, project_runtime_events_to_display


@dataclass(frozen=True, slots=True)
class RuntimeEventReplayService:
    store: RuntimeEventStore

    async def replay_runtime_events(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        limit: int = 1000,
    ) -> list[RuntimeEventRecord]:
        after_sequence = await self._after_sequence(run_id, last_event_id, limit=limit)
        return await self.store.replay_runtime_events(
            run_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    async def replay_display_events(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        limit: int = 1000,
    ) -> list[DisplayEvent]:
        after_sequence = await self._after_sequence(run_id, last_event_id, limit=limit)

        display_events: list[DisplayEvent] = []
        while len(display_events) < limit:
            runtime_events = await self.store.replay_runtime_events(
                run_id,
                after_sequence=after_sequence,
                limit=limit,
            )
            if not runtime_events:
                break
            display_events.extend(project_runtime_events_to_display(runtime_events))
            sequenced_events = [
                event for event in runtime_events if event.sequence is not None
            ]
            if not sequenced_events:
                break
            after_sequence = max(event.sequence for event in sequenced_events)
        return display_events[:limit]

    async def _after_sequence(
        self,
        run_id: str,
        last_event_id: str | None,
        *,
        limit: int,
    ) -> int:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if last_event_id is None:
            return 0
        last_event = await self.store.load_runtime_event(last_event_id)
        if last_event is None or last_event.run_id != run_id:
            raise KeyError(f"runtime event not found: {last_event_id}")
        if last_event.sequence is None:
            raise RuntimeError(f"runtime event has no replay sequence: {last_event_id}")
        return last_event.sequence
