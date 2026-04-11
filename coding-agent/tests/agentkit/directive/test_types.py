import pytest
from agentkit.directive.types import (
    Directive,
    Approve,
    Reject,
    AskUser,
    Checkpoint,
    MemoryRecord,
)


class TestDirectiveTypes:
    def test_approve_is_directive(self):
        d = Approve()
        assert isinstance(d, Directive)

    def test_reject_carries_reason(self):
        d = Reject(reason="dangerous command")
        assert d.reason == "dangerous command"

    def test_ask_user_carries_question(self):
        d = AskUser(question="Run rm -rf?")
        assert d.question == "Run rm -rf?"

    def test_checkpoint_carries_data(self):
        d = Checkpoint(plugin_id="memory", state={"key": "value"})
        assert d.plugin_id == "memory"
        assert d.state == {"key": "value"}

    def test_memory_record_fields(self):
        d = MemoryRecord(
            summary="User fixed a bug in auth.py",
            tags=["bugfix", "auth"],
            importance=0.8,
        )
        assert d.summary == "User fixed a bug in auth.py"
        assert d.tags == ["bugfix", "auth"]
        assert d.importance == 0.8

    def test_directive_is_frozen(self):
        d = Approve()
        with pytest.raises(AttributeError):
            d.kind = "reject"  # type: ignore[attr-defined]

    def test_all_directives_have_kind(self):
        assert Approve().kind == "approve"
        assert Reject(reason="no").kind == "reject"
        assert AskUser(question="?").kind == "ask_user"
        assert Checkpoint(plugin_id="x", state={}).kind == "checkpoint"
        assert MemoryRecord(summary="x").kind == "memory_record"
