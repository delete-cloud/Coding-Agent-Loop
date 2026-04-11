import pytest
from agentkit.tape.anchor import Anchor, AnchorType
from agentkit.tape.models import Entry


class TestAnchor:
    def test_anchor_is_entry_subclass(self):
        anchor = Anchor(
            anchor_type="handoff",
            payload={"content": "summary"},
        )
        assert isinstance(anchor, Entry)

    def test_kind_is_always_anchor(self):
        anchor = Anchor(
            anchor_type="handoff",
            payload={"content": "summary"},
        )
        assert anchor.kind == "anchor"

    def test_anchor_is_frozen(self):
        anchor = Anchor(
            anchor_type="handoff",
            payload={"content": "summary"},
        )
        with pytest.raises(AttributeError):
            setattr(anchor, "anchor_type", "fold")

    def test_is_handoff_property(self):
        assert Anchor(anchor_type="handoff", payload={}).is_handoff is True
        assert Anchor(anchor_type="topic_start", payload={}).is_handoff is False

    def test_fold_boundary_property(self):
        assert Anchor(anchor_type="fold", payload={}).fold_boundary is True
        assert Anchor(anchor_type="topic_end", payload={}).fold_boundary is True
        assert Anchor(anchor_type="handoff", payload={}).fold_boundary is False
        assert Anchor(anchor_type="topic_start", payload={}).fold_boundary is False
        assert Anchor(anchor_type="context", payload={}).fold_boundary is False

    def test_context_anchor_is_not_handoff(self):
        anchor = Anchor(anchor_type="context", payload={"content": "plain anchor"})
        assert anchor.is_handoff is False

    def test_source_ids_default_empty(self):
        anchor = Anchor(anchor_type="handoff", payload={})
        assert anchor.source_ids == ()

    def test_source_ids_stored(self):
        anchor = Anchor(
            anchor_type="handoff",
            payload={},
            source_ids=("id-1", "id-2"),
        )
        assert anchor.source_ids == ("id-1", "id-2")

    def test_to_dict_includes_anchor_fields(self):
        anchor = Anchor(
            anchor_type="handoff",
            payload={"content": "summary"},
            source_ids=("a", "b"),
            meta={"prefix": "Context Summary"},
        )
        d = anchor.to_dict()
        assert d["kind"] == "anchor"
        assert d["anchor_type"] == "handoff"
        assert d["source_ids"] == ["a", "b"]
        assert d["meta"]["prefix"] == "Context Summary"
        assert "id" in d
        assert "timestamp" in d

    def test_to_dict_omits_empty_source_ids(self):
        anchor = Anchor(anchor_type="fold", payload={})
        d = anchor.to_dict()
        assert "source_ids" not in d

    def test_from_dict_roundtrip(self):
        original = Anchor(
            anchor_type="topic_end",
            payload={"content": "topic done"},
            source_ids=("x", "y", "z"),
            meta={"topic_id": "t1"},
        )
        restored = Anchor.from_dict(original.to_dict())
        assert isinstance(restored, Anchor)
        assert restored.anchor_type == "topic_end"
        assert restored.source_ids == ("x", "y", "z")
        assert restored.meta["topic_id"] == "t1"
        assert restored.id == original.id

    def test_from_dict_without_source_ids(self):
        d = {
            "id": "a1",
            "kind": "anchor",
            "payload": {"content": "summary"},
            "timestamp": 1000.0,
            "anchor_type": "handoff",
        }
        anchor = Anchor.from_dict(d)
        assert anchor.source_ids == ()
        assert anchor.anchor_type == "handoff"

    def test_meta_preserved(self):
        anchor = Anchor(
            anchor_type="handoff",
            payload={"content": "s"},
            meta={"folded_topics": ["t1", "t2"], "prefix": "Context Summary"},
        )
        assert anchor.meta["folded_topics"] == ["t1", "t2"]
