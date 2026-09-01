from __future__ import annotations
from datetime import UTC, datetime

import pytest
from agentkit.runtime.messages import RuntimeMessageKind
from agentkit.runtime.pipeline import PipelineContext
from agentkit.tape.tape import Tape
from coding_agent.events.child_projection import (
    build_parent_model_context_facts,
    include_in_parent_context,
    include_in_parent_wire,
)
from coding_agent.runs.turn_execution import build_phase_e_turn_model_context
from coding_agent.stores.runtime_store import EventRecord
from coding_agent.tools.subagent import _publish_subagent_summary


def test_child_internal_facts_are_excluded_from_parent_context_and_wire() -> None:
    child_payload = {
        "run_id": "child-run",
        "parent_run_id": "parent-run",
        "parent_effect_id": "parent-effect",
        "subagent_child": True,
        "skip_parent_context": True,
    }

    assert include_in_parent_context(child_payload) is False
    assert include_in_parent_wire("assistant_message", child_payload) is False
    assert include_in_parent_wire("tool_call", child_payload) is False
    assert (
        include_in_parent_wire(
            "approval_requested",
            {
                **child_payload,
                "target_run_id": "child-run",
                "target_parent_effect_id": "parent-effect",
            },
        )
        is True
    )


def test_new_runtime_child_terminal_output_is_one_parent_tool_result_fact() -> None:
    child_terminal = {
        "subagent_child": True,
        "skip_parent_context": True,
        "content": "internal child terminal",
    }
    parent_tool_result = {
        "tool_call_id": "parent-call",
        "tool_name": "subagent",
        "content": "one parent result",
    }

    projected = [
        payload
        for event_kind, payload in (
            ("assistant_message", child_terminal),
            ("tool_result", parent_tool_result),
        )
        if include_in_parent_context(payload)
        and include_in_parent_wire(event_kind, payload)
    ]

    assert projected == [parent_tool_result]


@pytest.mark.asyncio
async def test_legacy_child_terminal_output_uses_runtime_message_bus() -> None:
    published: list[tuple[object, ...]] = []

    async def publish(*args, **kwargs):
        published.append((*args, kwargs))

    context = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        config={"subagent_message_publisher": publish},
    )
    await _publish_subagent_summary(
        context,
        session_id="parent-session",
        summary="legacy child summary",
        child_agent_id="child-agent",
    )

    assert published == [
        (
            "parent-session",
            "legacy child summary",
            {
                "message_id": None,
                "metadata": {
                    "source": "subagent",
                    "child_agent_id": "child-agent",
                },
            },
        )
    ]
    assert RuntimeMessageKind.SUBAGENT_MESSAGE.value == "subagent_message"


def test_parent_model_context_builder_filters_child_facts() -> None:
    facts = (
        EventRecord(
            event_id="parent",
            session_id="session",
            event_kind="assistant_message",
            payload={"run_id": "parent-run", "text": "keep"},
            created_at=datetime(2026, 9, 1, tzinfo=UTC),
        ),
        EventRecord(
            event_id="child",
            session_id="session",
            event_kind="assistant_message",
            payload={
                "run_id": "child-run",
                "text": "hide",
                "subagent_child": True,
                "skip_parent_context": True,
            },
            created_at=datetime(2026, 9, 1, tzinfo=UTC),
        ),
    )

    expected = (facts[0],)
    assert build_parent_model_context_facts(facts) == expected
    assert build_phase_e_turn_model_context(facts) == expected
