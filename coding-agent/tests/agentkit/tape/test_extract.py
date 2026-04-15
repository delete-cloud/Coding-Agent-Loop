"""Tests for agentkit.tape.extract — structured turn extraction from tape."""

from agentkit.tape.extract import (
    ToolCallRecord,
    Visibility,
    extract_turns,
)
from agentkit.tape.models import Entry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user(content: str, **meta_kw: object) -> Entry:
    return Entry(
        kind="message", payload={"role": "user", "content": content}, meta=dict(meta_kw)
    )


def _assistant(content: str, **meta_kw: object) -> Entry:
    return Entry(
        kind="message",
        payload={"role": "assistant", "content": content},
        meta=dict(meta_kw),
    )


def _tool_call(
    call_id: str,
    name: str,
    arguments: dict[str, object] | None = None,
    **meta_kw: object,
) -> Entry:
    return Entry(
        kind="tool_call",
        payload={
            "id": call_id,
            "name": name,
            "arguments": arguments or {},
            "role": "assistant",
        },
        meta=dict(meta_kw),
    )


def _tool_result(call_id: str, content: str, **meta_kw: object) -> Entry:
    return Entry(
        kind="tool_result",
        payload={"tool_call_id": call_id, "content": content},
        meta=dict(meta_kw),
    )


def _anchor(content: str = "", **meta_kw: object) -> Entry:
    return Entry(kind="anchor", payload={"content": content}, meta=dict(meta_kw))


# ---------------------------------------------------------------------------
# Basic extraction
# ---------------------------------------------------------------------------


class TestBasicExtraction:
    def test_single_turn_no_tools(self):
        entries = [
            _user("hello"),
            _assistant("hi there"),
        ]
        turns = extract_turns(entries)
        assert len(turns) == 1
        assert turns[0].user_input == "hello"
        assert turns[0].tool_calls == ()
        assert turns[0].final_output == "hi there"

    def test_single_turn_with_one_tool_call(self):
        entries = [
            _user("read foo.py"),
            _tool_call("tc1", "file_read", {"path": "foo.py"}),
            _tool_result("tc1", "contents of foo"),
            _assistant("here is foo.py"),
        ]
        turns = extract_turns(entries)
        assert len(turns) == 1
        turn = turns[0]
        assert turn.user_input == "read foo.py"
        assert len(turn.tool_calls) == 1
        assert turn.tool_calls[0] == ToolCallRecord(
            call_id="tc1",
            name="file_read",
            arguments={"path": "foo.py"},
            result_content="contents of foo",
        )
        assert turn.final_output == "here is foo.py"

    def test_two_turns(self):
        entries = [
            _user("first"),
            _assistant("response 1"),
            _user("second"),
            _assistant("response 2"),
        ]
        turns = extract_turns(entries)
        assert len(turns) == 2
        assert turns[0].user_input == "first"
        assert turns[0].final_output == "response 1"
        assert turns[1].user_input == "second"
        assert turns[1].final_output == "response 2"

    def test_no_user_messages_yields_empty(self):
        entries = [
            _assistant("orphan message"),
            _tool_call("tc1", "bash_run"),
        ]
        turns = extract_turns(entries)
        assert turns == []

    def test_turn_without_final_output(self):
        """Turn that ended at max steps — no assistant text after tools."""
        entries = [
            _user("do something"),
            _tool_call("tc1", "bash_run", {"command": "echo hi"}),
            _tool_result("tc1", "hi"),
        ]
        turns = extract_turns(entries)
        assert len(turns) == 1
        assert turns[0].final_output is None


# ---------------------------------------------------------------------------
# Batch / parallel tool calls
# ---------------------------------------------------------------------------


class TestBatchToolCalls:
    def test_parallel_calls_paired_correctly(self):
        """3 tool_calls then 3 tool_results — typical parallel execution."""
        entries = [
            _user("search three files"),
            _tool_call("tc1", "file_read", {"path": "a.py"}),
            _tool_call("tc2", "file_read", {"path": "b.py"}),
            _tool_call("tc3", "file_read", {"path": "c.py"}),
            _tool_result("tc1", "aaa"),
            _tool_result("tc2", "bbb"),
            _tool_result("tc3", "ccc"),
            _assistant("done"),
        ]
        turns = extract_turns(entries)
        assert len(turns) == 1
        records = turns[0].tool_calls
        assert len(records) == 3
        assert records[0].name == "file_read"
        assert records[0].result_content == "aaa"
        assert records[1].result_content == "bbb"
        assert records[2].result_content == "ccc"

    def test_unmatched_result_silently_dropped(self):
        """tool_result with unknown call_id — e.g. from child tape leakage."""
        entries = [
            _user("go"),
            _tool_call("tc1", "bash_run"),
            _tool_result("tc1", "ok"),
            _tool_result("tc-orphan", "should be ignored"),
            _assistant("done"),
        ]
        turns = extract_turns(entries)
        assert len(turns) == 1
        assert len(turns[0].tool_calls) == 1
        assert turns[0].tool_calls[0].result_content == "ok"

    def test_tool_call_without_result(self):
        """tool_call that never got a result (e.g. tape truncated)."""
        entries = [
            _user("go"),
            _tool_call("tc1", "bash_run"),
        ]
        turns = extract_turns(entries)
        assert turns[0].tool_calls[0].result_content is None

    def test_batched_tool_call_payload_is_expanded_into_multiple_records(self):
        entries = [
            _user("search two files"),
            Entry(
                kind="tool_call",
                payload={
                    "tool_calls": [
                        {
                            "id": "tc1",
                            "function": {
                                "name": "file_read",
                                "arguments": {"path": "a.py"},
                            },
                        },
                        {
                            "id": "tc2",
                            "function": {
                                "name": "file_read",
                                "arguments": {"path": "b.py"},
                            },
                        },
                    ]
                },
            ),
            _tool_result("tc1", "aaa"),
            _tool_result("tc2", "bbb"),
            _assistant("done"),
        ]

        turns = extract_turns(entries)

        assert len(turns) == 1
        assert [record.call_id for record in turns[0].tool_calls] == ["tc1", "tc2"]
        assert [record.name for record in turns[0].tool_calls] == [
            "file_read",
            "file_read",
        ]
        assert [record.arguments for record in turns[0].tool_calls] == [
            {"path": "a.py"},
            {"path": "b.py"},
        ]
        assert [record.result_content for record in turns[0].tool_calls] == [
            "aaa",
            "bbb",
        ]

    def test_flat_batched_tool_call_payload_is_expanded_into_multiple_records(self):
        entries = [
            _user("search two files"),
            Entry(
                kind="tool_call",
                payload={
                    "tool_calls": [
                        {
                            "id": "tc1",
                            "name": "file_read",
                            "arguments": {"path": "a.py"},
                        },
                        {
                            "id": "tc2",
                            "name": "file_read",
                            "arguments": {"path": "b.py"},
                        },
                    ]
                },
            ),
            _tool_result("tc1", "aaa"),
            _tool_result("tc2", "bbb"),
            _assistant("done"),
        ]

        turns = extract_turns(entries)

        assert len(turns) == 1
        assert [record.call_id for record in turns[0].tool_calls] == ["tc1", "tc2"]
        assert [record.name for record in turns[0].tool_calls] == [
            "file_read",
            "file_read",
        ]
        assert [record.arguments for record in turns[0].tool_calls] == [
            {"path": "a.py"},
            {"path": "b.py"},
        ]
        assert [record.result_content for record in turns[0].tool_calls] == [
            "aaa",
            "bbb",
        ]

    def test_empty_tool_call_ids_do_not_pair_results(self):
        entries = [
            _user("go"),
            _tool_call("", "file_read", {"path": "a.py"}),
            _tool_call("", "file_read", {"path": "b.py"}),
            _tool_result("", "ambiguous result"),
            _assistant("done"),
        ]

        turns = extract_turns(entries)

        assert len(turns) == 1
        assert [record.call_id for record in turns[0].tool_calls] == ["", ""]
        assert [record.result_content for record in turns[0].tool_calls] == [None, None]

    def test_empty_batched_tool_call_payload_does_not_create_blank_record(self):
        entries = [
            _user("go"),
            Entry(kind="tool_call", payload={"tool_calls": []}),
            _assistant("done"),
        ]

        turns = extract_turns(entries)

        assert len(turns) == 1
        assert turns[0].tool_calls == ()

    def test_malformed_batched_tool_call_payload_does_not_create_blank_record(self):
        entries = [
            _user("go"),
            Entry(kind="tool_call", payload={"tool_calls": [None, "bad", 3]}),
            _assistant("done"),
        ]

        turns = extract_turns(entries)

        assert len(turns) == 1
        assert turns[0].tool_calls == ()


# ---------------------------------------------------------------------------
# skip_context / subagent handling
# ---------------------------------------------------------------------------


class TestSkipContext:
    def test_child_user_message_does_not_split_parent_turn(self):
        """Reproduce the cd9633a6 tape pattern: parent subagent call injects
        child entries with skip_context=True into the parent tape."""
        entries = [
            _user("parent task"),
            _tool_call("tc-sub", "subagent", {"goal": "child task"}),
            # --- child entries injected by _append_child_trace_to_parent ---
            _user("child task", skip_context=True, subagent_child=True),
            _assistant("child done", skip_context=True, subagent_child=True),
            _anchor("child topic", skip_context=True, subagent_child=True),
            # --- back to parent ---
            _tool_result("tc-sub", "Subagent completed: child done"),
            _assistant("parent done"),
        ]
        turns = extract_turns(entries, visibility=Visibility.VISIBLE)
        assert len(turns) == 1
        turn = turns[0]
        assert turn.user_input == "parent task"
        assert len(turn.tool_calls) == 1
        assert turn.tool_calls[0].name == "subagent"
        assert turn.tool_calls[0].result_content == "Subagent completed: child done"
        assert turn.final_output == "parent done"

    def test_skip_context_tool_calls_excluded_in_visible_mode(self):
        entries = [
            _user("go"),
            _tool_call("tc1", "file_read", skip_context=True),
            _tool_result("tc1", "hidden", skip_context=True),
            _tool_call("tc2", "bash_run"),
            _tool_result("tc2", "visible result"),
            _assistant("done"),
        ]
        turns = extract_turns(entries, visibility=Visibility.VISIBLE)
        assert len(turns[0].tool_calls) == 1
        assert turns[0].tool_calls[0].name == "bash_run"

    def test_raw_mode_includes_everything(self):
        """RAW mode: child user message DOES create a new turn boundary."""
        entries = [
            _user("parent task"),
            _tool_call("tc-sub", "subagent", {"goal": "child task"}),
            _user("child task", skip_context=True, subagent_child=True),
            _assistant("child done", skip_context=True, subagent_child=True),
            _tool_result("tc-sub", "Subagent completed: child done"),
            _assistant("parent done"),
        ]
        turns = extract_turns(entries, visibility=Visibility.RAW)
        # RAW sees two user messages → two turns
        assert len(turns) == 2
        assert turns[0].user_input == "parent task"
        assert turns[1].user_input == "child task"

    def test_raw_mode_includes_skip_context_tool_calls(self):
        entries = [
            _user("go"),
            _tool_call("tc1", "file_read", skip_context=True),
            _tool_result("tc1", "hidden content", skip_context=True),
            _assistant("done"),
        ]
        turns = extract_turns(entries, visibility=Visibility.RAW)
        assert len(turns[0].tool_calls) == 1
        assert turns[0].tool_calls[0].name == "file_read"


# ---------------------------------------------------------------------------
# Multiple tool rounds within one turn
# ---------------------------------------------------------------------------


class TestMultipleToolRounds:
    def test_two_rounds_of_tool_calls(self):
        """Agent does tool round 1, gets results, then does tool round 2."""
        entries = [
            _user("fix the bug"),
            # Round 1
            _tool_call("tc1", "file_read", {"path": "bug.py"}),
            _tool_result("tc1", "buggy code"),
            # Intermediate assistant message
            _assistant("I see the issue, let me fix it"),
            # Round 2
            _tool_call("tc2", "file_replace", {"path": "bug.py", "new": "fixed"}),
            _tool_result("tc2", "file updated"),
            _assistant("Fixed the bug"),
        ]
        turns = extract_turns(entries)
        assert len(turns) == 1
        turn = turns[0]
        assert len(turn.tool_calls) == 2
        assert turn.tool_calls[0].name == "file_read"
        assert turn.tool_calls[1].name == "file_replace"
        # final_output is the LAST assistant message
        assert turn.final_output == "Fixed the bug"


# ---------------------------------------------------------------------------
# Anchors and events (non-message, non-tool entries)
# ---------------------------------------------------------------------------


class TestNonToolEntries:
    def test_anchors_do_not_affect_extraction(self):
        entries = [
            _anchor("Topic Start", topic_id="t1"),
            _user("hello"),
            _anchor("some context"),
            _assistant("world"),
        ]
        turns = extract_turns(entries)
        assert len(turns) == 1
        assert turns[0].user_input == "hello"
        assert turns[0].final_output == "world"

    def test_event_entries_ignored(self):
        entries = [
            _user("go"),
            Entry(kind="event", payload={"event_type": "doom_detected"}),
            _assistant("done"),
        ]
        turns = extract_turns(entries)
        assert len(turns) == 1
        assert turns[0].tool_calls == ()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_entries(self):
        assert extract_turns([]) == []
        assert extract_turns(()) == []

    def test_non_string_assistant_content_is_not_treated_as_final_output(self):
        entries = [
            _user("go"),
            Entry(
                kind="message",
                payload={
                    "role": "assistant",
                    "content": [{"type": "text", "text": "hi"}],
                },
            ),
        ]

        turns = extract_turns(entries)

        assert len(turns) == 1
        assert turns[0].final_output is None

    def test_assistant_message_with_empty_content_not_treated_as_final(self):
        """Assistant messages with empty/None content are not final output."""
        entries = [
            _user("go"),
            _assistant(""),
            _tool_call("tc1", "bash_run"),
            _tool_result("tc1", "ok"),
        ]
        turns = extract_turns(entries)
        assert turns[0].final_output is None

    def test_entries_before_first_user_message_ignored(self):
        """System bootstrap entries before any user message are skipped."""
        entries = [
            _assistant("I am ready"),
            _anchor("system init"),
            _user("start"),
            _assistant("ok"),
        ]
        turns = extract_turns(entries)
        assert len(turns) == 1
        assert turns[0].user_input == "start"
