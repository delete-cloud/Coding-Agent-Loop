"""Red contracts for the intended ``coding_agent.harness.restore`` module."""

from __future__ import annotations


FENCE = {"session_id": "session-7", "owner_id": "daemon-b", "epoch": 12}


def test_restore_marks_adr_0075_superseded_runs_and_does_not_delete_them() -> None:
    from coding_agent.harness.restore import RestoreStore

    store = RestoreStore()
    store.add_run("run-before", started_at="2026-08-19T10:00:00Z")
    store.add_run("run-after", started_at="2026-08-19T10:10:00Z")
    store.add_event("run-after", event_id="event-9")
    store.add_checkpoint(
        checkpoint_id="checkpoint-2",
        created_at="2026-08-19T10:05:00Z",
    )

    store.restore(
        fence=FENCE,
        checkpoint_id="checkpoint-2",
        restored_at="2026-08-19T10:15:00Z",
    )

    assert store.audit_run_ids() == ("run-before", "run-after")
    assert store.active_run_ids() == ("run-before",)
    assert store.run("run-after").superseded_at == "2026-08-19T10:15:00Z"
    assert store.run("run-after").superseded_by_checkpoint_id == "checkpoint-2"
    assert store.event_ids("run-after") == ("event-9",)


def test_restore_linearizes_mailbox_lane_cuts_and_effects() -> None:
    from coding_agent.harness.restore import RestoreStore

    store = RestoreStore()
    store.add_checkpoint(
        checkpoint_id="checkpoint-2",
        lane_cuts={"user": 7, "approval": 3},
        effect_states={40: "prepared"},
    )
    store.add_run("run-post-checkpoint", started_at="2026-08-19T10:10:00Z")
    store.append_mailbox("user", sequence=8, payload="rolled-back prompt")
    store.append_mailbox("approval", sequence=4, payload="rolled-back approval")
    store.set_effect_state(effect_id=40, state="dispatched")

    result = store.restore(
        fence=FENCE,
        checkpoint_id="checkpoint-2",
        restored_at="2026-08-19T10:15:00Z",
    )

    assert result.transaction_count == 1
    assert result.atomic_writes == (
        ("mailbox_lane_cut", "approval", 3),
        ("mailbox_lane_cut", "user", 7),
        ("effect", 40, "prepared"),
        ("run_superseded", "run-post-checkpoint", "checkpoint-2"),
        ("projection_epoch", 1),
    )
    assert store.run("run-post-checkpoint").superseded_at == "2026-08-19T10:15:00Z"
    assert store.visible_mailbox_sequences("user") == ()
    assert store.visible_mailbox_sequences("approval") == ()
    assert store.effect_state(40) == "prepared"


def test_session_seq_is_monotonic_per_session_across_restore_epochs() -> None:
    from coding_agent.harness.restore import RestoreStore

    store = RestoreStore()
    store.add_checkpoint(checkpoint_id="checkpoint-2")
    first = store.append_event("session-7", projection_epoch=0, kind="turn_started")
    second = store.append_event("session-7", projection_epoch=0, kind="tool_called")
    store.restore(fence=FENCE, checkpoint_id="checkpoint-2")
    third = store.append_event("session-7", projection_epoch=1, kind="turn_started")

    assert (first.session_seq, second.session_seq, third.session_seq) == (1, 2, 3)
    assert (first.projection_epoch, second.projection_epoch, third.projection_epoch) == (
        0,
        0,
        1,
    )


def test_raw_cursor_follows_physical_log_across_epochs() -> None:
    from coding_agent.harness.restore import RestoreStore

    store = RestoreStore()
    store.add_checkpoint(checkpoint_id="checkpoint-2")
    store.append_event("session-7", projection_epoch=0, kind="before_restore")
    raw_cursor = store.raw_cursor("session-7", session_seq=1)
    store.restore(fence=FENCE, checkpoint_id="checkpoint-2")
    store.append_event("session-7", projection_epoch=1, kind="after_restore")

    replay = store.read_raw("session-7", after=raw_cursor)

    assert raw_cursor == "raw:session-7:1"
    assert tuple((event.session_seq, event.kind) for event in replay) == (
        (2, "restore_committed"),
        (3, "after_restore"),
    )


def test_delta_and_settled_cursors_bind_projection_and_epoch() -> None:
    from coding_agent.harness.restore import CursorBindingError
    from coding_agent.harness.restore import RestoreStore

    store = RestoreStore()
    delta = store.projection_cursor(
        kind="delta",
        session_id="session-7",
        projection="timeline-v2",
        epoch=1,
        offset=9,
    )
    settled = store.projection_cursor(
        kind="settled",
        session_id="session-7",
        projection="timeline-v2",
        epoch=1,
        offset=6,
    )

    delta_error = store.capture_error(
        CursorBindingError,
        store.read_projection,
        cursor=delta,
        projection="timeline-v3",
        epoch=1,
    )
    settled_error = store.capture_error(
        CursorBindingError,
        store.read_projection,
        cursor=settled,
        projection="timeline-v2",
        epoch=2,
    )

    assert delta == "delta:session-7:timeline-v2:1:9"
    assert settled == "settled:session-7:timeline-v2:1:6"
    assert delta_error.as_dict() == {
        "code": "cursor_projection_mismatch",
        "expected_projection": "timeline-v2",
        "actual_projection": "timeline-v3",
    }
    assert settled_error.as_dict() == {
        "code": "cursor_epoch_mismatch",
        "expected_epoch": 1,
        "actual_epoch": 2,
    }


def test_cross_host_key_expired_contract_lands_at_p2() -> None:
    from coding_agent.harness.restore import RestoreStore

    store = RestoreStore(retention_floor=20)

    replay = store.resolve_cross_host_key_expired(
        session_id="session-7",
        expired_cursor="raw:session-7:12",
        trusted_handoff=None,
    )
    handoff = store.resolve_cross_host_key_expired(
        session_id="session-7",
        expired_cursor="raw:session-7:12",
        trusted_handoff={"host_id": "host-b", "session_seq": 31},
    )

    assert replay.as_dict() == {
        "action": "replay_from_retention_floor",
        "retention_floor": 20,
        "phase": "P2",
    }
    assert handoff.as_dict() == {
        "action": "accept_trusted_handoff",
        "host_id": "host-b",
        "session_seq": 31,
        "phase": "P2",
    }
