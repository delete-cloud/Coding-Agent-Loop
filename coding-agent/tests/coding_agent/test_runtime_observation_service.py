from __future__ import annotations

from dataclasses import dataclass, field

from coding_agent.agent_observability import AgentObservationEvent
from coding_agent.runs import (
    IsolationPolicy,
    LocalDaemonExecutorRef,
    LocalPathWorkspaceRef,
    RunTarget,
)
from coding_agent.runs.observation import RuntimeObservationService


class RecordingObservationStore:
    def __init__(self) -> None:
        self.events: list[AgentObservationEvent] = []

    def append(self, event: AgentObservationEvent) -> None:
        self.events.append(event)


@dataclass(frozen=True)
class FakeResumeContext:
    previous_run_id: str

    def metadata(self) -> dict[str, str]:
        return {"previous_run_id": self.previous_run_id}


@dataclass
class FakeSession:
    id: str = "session-1"
    default_run_target: RunTarget = field(
        default_factory=lambda: RunTarget(
            workspace=LocalPathWorkspaceRef(path="/repo"),
            executor=LocalDaemonExecutorRef(),
            isolation=IsolationPolicy(kind="default_local_sandbox"),
        )
    )


class FakeTape:
    tape_id = "tape-1"


class FakeRuntimeContext:
    def __init__(self) -> None:
        self.config: dict[str, object] = {}
        self.tape = FakeTape()


def test_runtime_observation_service_records_turn_start_and_completion() -> None:
    store = RecordingObservationStore()
    service = RuntimeObservationService(store)
    ctx = FakeRuntimeContext()

    recorder = service.start(
        session=FakeSession(),
        ctx=ctx,
        run_id="run-1",
        prompt="raw prompt",
        resume_context=FakeResumeContext(previous_run_id="run-0"),
    )
    service.complete(recorder, ctx=ctx, turn_status="failed")

    assert ctx.config["agent_observation_recorder"] is recorder
    assert [event.kind for event in store.events] == ["turn.started", "turn.failed"]
    assert store.events[0].attributes["user_length"] == len("raw prompt")
    assert store.events[0].attributes["previous_run_id"] == "run-0"
    assert store.events[0].attributes["tape_id"] == "tape-1"
    assert store.events[0].attributes["execution_placement"] == "server_embedded"
    assert store.events[0].attributes["executor_kind"] == "local_daemon"
    assert store.events[0].attributes["workspace_surface"] == "local_workspace"
    assert store.events[0].attributes["execution_plane"] == "control_plane"
    assert store.events[1].status == "error"


def test_runtime_observation_service_ignores_context_without_config() -> None:
    store = RecordingObservationStore()
    service = RuntimeObservationService(store)
    ctx = object()

    recorder = service.start(
        session=FakeSession(),
        ctx=ctx,
        run_id="run-1",
        prompt="raw prompt",
    )
    service.complete(recorder, ctx=ctx, turn_status="completed")

    assert recorder is None
    assert store.events == []
