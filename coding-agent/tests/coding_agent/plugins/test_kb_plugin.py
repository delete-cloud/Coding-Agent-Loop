from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentkit.observability import SpanRecord
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from coding_agent.kb import DocumentChunk, KB, KBSearchResult
from coding_agent.plugins.kb import KBPlugin


def _fake_embed(texts: list[str]) -> list[list[float]]:
    return [[float(i)] * 8 for i, _ in enumerate(texts)]


class RecordingObservationSink:
    def __init__(self) -> None:
        self.spans: list[SpanRecord] = []

    def record_span(self, span: SpanRecord) -> None:
        self.spans.append(span)

    def record_event(self, event) -> None:
        del event


class _MountContext:
    def __init__(self, sink: RecordingObservationSink) -> None:
        self.config = {"observation_sink": sink}


def _span_by_name(sink: RecordingObservationSink, name: str) -> SpanRecord:
    matches = [span for span in sink.spans if span.name == name]
    assert len(matches) == 1
    return matches[0]


def _serialized_attributes(span: SpanRecord) -> str:
    return repr(sorted(span.attributes.items()))


class TestKBPluginInit:
    def test_state_key(self):
        plugin = KBPlugin(
            db_path=Path("/tmp/test_kb"),
            embedding_dim=8,
            embedding_fn=_fake_embed,
        )

        assert plugin.state_key == "kb"

    def test_hooks_registered(self):
        plugin = KBPlugin(
            db_path=Path("/tmp/test_kb"),
            embedding_dim=8,
            embedding_fn=_fake_embed,
        )

        hooks = plugin.hooks()

        assert "mount" in hooks
        assert "build_context" in hooks
        assert len(hooks) == 2


class TestKBPluginMount:
    def test_mount_creates_kb_instance(self, tmp_path: Path):
        plugin = KBPlugin(
            db_path=tmp_path / "kb_db",
            embedding_base_url="https://embed.example/v1",
            embedding_dim=8,
            embedding_fn=_fake_embed,
        )

        state = plugin.do_mount()

        assert "kb" in state
        assert "has_table" in state
        assert state["has_table"] is False
        assert state["kb"].embedding_base_url == "https://embed.example/v1"

    def test_mount_detects_existing_table(self, tmp_path: Path):
        kb = KB(db_path=tmp_path / "kb_db", embedding_dim=8, embedding_fn=_fake_embed)
        asyncio.run(kb.index_file(Path("test.md"), "some content for indexing"))

        plugin = KBPlugin(
            db_path=tmp_path / "kb_db",
            embedding_dim=8,
            embedding_fn=_fake_embed,
        )

        state = plugin.do_mount()

        assert state["has_table"] is True


class TestBuildContextNoTable:
    def test_returns_empty_when_no_table(self, tmp_path: Path):
        plugin = KBPlugin(
            db_path=tmp_path / "kb_db",
            embedding_dim=8,
            embedding_fn=_fake_embed,
        )
        plugin.do_mount()
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hello"}))

        result = plugin.build_context(tape=tape)

        assert result == []


class TestBuildContextSearch:
    @pytest.fixture()
    def indexed_plugin(self, tmp_path: Path) -> KBPlugin:
        db_path = tmp_path / "kb_db"
        kb = KB(db_path=db_path, embedding_dim=8, embedding_fn=_fake_embed)
        asyncio.run(
            kb.index_file(
                Path("src/auth.py"),
                "Authentication module with JWT token validation",
            )
        )
        asyncio.run(
            kb.index_file(
                Path("docs/api.md"),
                "API documentation for the REST endpoints",
            )
        )

        plugin = KBPlugin(
            db_path=db_path,
            embedding_dim=8,
            top_k=5,
            embedding_fn=_fake_embed,
        )
        plugin.do_mount()
        return plugin

    def test_first_call_triggers_search(self, indexed_plugin: KBPlugin):
        tape = Tape()
        tape.append(
            Entry(
                kind="message",
                payload={"role": "user", "content": "How does auth work?"},
            )
        )

        result = indexed_plugin.build_context(tape=tape)

        assert isinstance(result, list)
        assert len(result) == 1
        msg = result[0]
        assert msg["role"] == "system"
        assert msg["content"].startswith("[Context Pack]")
        assert "## Repo references" in msg["content"]

    def test_context_pack_injection_uses_build_context_without_pipeline_rewrite(
        self, indexed_plugin: KBPlugin
    ):
        hooks = indexed_plugin.hooks()
        tape = Tape()
        tape.append(
            Entry(
                kind="message",
                payload={"role": "user", "content": "How does auth work?"},
            )
        )

        result = hooks["build_context"](tape=tape)

        assert hooks.keys() == {"mount", "build_context"}
        assert len(result) == 1
        assert result[0]["role"] == "system"
        assert result[0]["content"].startswith("[Context Pack]")
        assert "## Repo references" in result[0]["content"]
        assert "- [Repo] src/auth.py" in result[0]["content"]

    def test_cache_hit_same_message(self, indexed_plugin: KBPlugin):
        tape = Tape()
        tape.append(
            Entry(
                kind="message",
                payload={"role": "user", "content": "How does auth work?"},
            )
        )

        result1 = indexed_plugin.build_context(tape=tape)
        result2 = indexed_plugin.build_context(tape=tape)

        assert result1 == result2
        assert indexed_plugin._snapshot is not None
        assert indexed_plugin._snapshot.last_user_msg == "How does auth work?"

    def test_new_user_message_triggers_fresh_search(self, indexed_plugin: KBPlugin):
        first_tape = Tape()
        first_tape.append(
            Entry(
                kind="message",
                payload={"role": "user", "content": "How does auth work?"},
            )
        )
        second_tape = Tape()
        second_tape.append(
            Entry(
                kind="message",
                payload={"role": "user", "content": "What API endpoints exist?"},
            )
        )

        calls: list[str] = []
        original_search = indexed_plugin._kb.search_sync

        def tracking_search(query: str, k: int = 5, *, corpora=None):
            del corpora
            calls.append(query)
            return original_search(query, k=k)

        indexed_plugin._kb.search_sync = tracking_search

        first = indexed_plugin.build_context(tape=first_tape)
        second = indexed_plugin.build_context(tape=second_tape)

        assert len(first) == 1
        assert len(second) == 1
        assert calls == ["How does auth work?", "What API endpoints exist?"]
        assert indexed_plugin._snapshot is not None
        assert indexed_plugin._snapshot.last_user_msg == "What API endpoints exist?"

    def test_search_corpora_passed_to_kb_search(self, tmp_path: Path):
        db_path = tmp_path / "kb_db"
        kb = KB(
            db_path=db_path,
            embedding_dim=8,
            embedding_fn=_fake_embed,
            corpus="sre",
        )
        asyncio.run(kb.index_file(Path("sre.md"), "SRE restore runbook"))
        plugin = KBPlugin(
            db_path=db_path,
            embedding_dim=8,
            top_k=5,
            search_corpora=["sre"],
            embedding_fn=_fake_embed,
        )
        plugin.do_mount()
        assert plugin._kb is not None
        captured: list[tuple[str, ...] | None] = []
        original_search = plugin._kb.search_sync

        def tracking_search(query: str, k: int = 5, *, corpora=None):
            captured.append(corpora)
            return original_search(query, k=k, corpora=corpora)

        plugin._kb.search_sync = tracking_search
        tape = Tape()
        tape.append(
            Entry(
                kind="message",
                payload={"role": "user", "content": "How do I restore?"},
            )
        )

        result = plugin.build_context(tape=tape)

        assert len(result) == 1
        assert captured == [("sre",)]

    def test_max_distance_filters_context_pack_results(self, tmp_path: Path):
        db_path = tmp_path / "kb_db"
        kb = KB(db_path=db_path, embedding_dim=8, embedding_fn=_fake_embed)
        asyncio.run(kb.index_file(Path("src/auth.py"), "seed table"))
        sink = RecordingObservationSink()
        plugin = KBPlugin(
            db_path=db_path,
            embedding_dim=8,
            top_k=5,
            max_distance=0.25,
            embedding_fn=_fake_embed,
        )
        plugin.do_mount(ctx=_MountContext(sink))
        assert plugin._kb is not None
        plugin._kb.search_sync = lambda query, k=5, *, corpora=None: [
            KBSearchResult(
                chunk=DocumentChunk(
                    id="good",
                    content="relevant auth runbook",
                    source="src/auth.py",
                    metadata={
                        "source_kind": "repo_file",
                        "source_id": "src/auth.py",
                        "repo_path": "src/auth.py",
                    },
                ),
                score=0.10,
            ),
            KBSearchResult(
                chunk=DocumentChunk(
                    id="bad",
                    content="distant billing note",
                    source="src/billing.py",
                    metadata={
                        "source_kind": "repo_file",
                        "source_id": "src/billing.py",
                        "repo_path": "src/billing.py",
                    },
                ),
                score=0.75,
            ),
        ]
        tape = Tape()
        tape.append(
            Entry(
                kind="message",
                payload={"role": "user", "content": "How does auth work?"},
            )
        )

        result = plugin.build_context(tape=tape)

        assert len(result) == 1
        assert "relevant auth runbook" in result[0]["content"]
        assert "distant billing note" not in result[0]["content"]
        search_span = _span_by_name(sink, "retrieval.kb.search")
        assert search_span.attributes["retrieval.candidate_count"] == 2
        assert search_span.attributes["retrieval.selected_count"] == 1
        assert search_span.attributes["retrieval.max_distance"] == 0.25

    def test_retrieval_observability_emits_counts_without_sensitive_attributes(
        self, indexed_plugin: KBPlugin
    ):
        sink = RecordingObservationSink()
        indexed_plugin.do_mount(ctx=_MountContext(sink))
        tape = Tape()
        tape.append(
            Entry(
                kind="message",
                payload={
                    "role": "user",
                    "content": "secret prompt auth token should not be exported",
                },
            )
        )

        indexed_plugin.build_context(tape=tape)

        search_span = _span_by_name(sink, "retrieval.kb.search")
        render_span = _span_by_name(sink, "context_pack.render")
        assert search_span.attributes == {
            "retrieval.cache_hit": False,
            "retrieval.candidate_count": 2,
            "retrieval.kb_chunk_count": 0,
            "retrieval.query_present": True,
            "retrieval.repo_file_count": 2,
            "retrieval.selected_count": 2,
            "retrieval.source_kind": "kb",
            "retrieval.test_failure_count": 0,
            "retrieval.top_k": 5,
        }
        assert render_span.attributes == {
            "pack.item_count": 2,
            "pack.kb_chunk_count": 0,
            "pack.repo_file_count": 2,
            "pack.section_count": 1,
            "pack.test_failure_count": 0,
        }
        for span in (search_span, render_span):
            serialized = _serialized_attributes(span)
            assert "secret prompt auth token" not in serialized
            assert "Authentication module" not in serialized
            assert "API documentation" not in serialized
            assert "src/auth.py" not in serialized
            assert "docs/api.md" not in serialized
            assert not any(
                forbidden in key
                for key in span.attributes
                for forbidden in (
                    "content",
                    "message",
                    "prompt",
                    "result",
                    "secret",
                    "text",
                )
            )

    def test_retrieval_observability_records_cache_hit_without_query_content(
        self, indexed_plugin: KBPlugin
    ):
        sink = RecordingObservationSink()
        indexed_plugin.do_mount(ctx=_MountContext(sink))
        tape = Tape()
        tape.append(
            Entry(
                kind="message",
                payload={"role": "user", "content": "How does auth work?"},
            )
        )

        indexed_plugin.build_context(tape=tape)
        indexed_plugin.build_context(tape=tape)

        search_spans = [
            span for span in sink.spans if span.name == "retrieval.kb.search"
        ]
        assert [span.attributes["retrieval.cache_hit"] for span in search_spans] == [
            False,
            True,
        ]
        assert search_spans[1].attributes["retrieval.selected_count"] == 2
        serialized_cache_hit = _serialized_attributes(search_spans[1])
        assert "How does auth work?" not in serialized_cache_hit
        assert "src/auth.py" not in serialized_cache_hit
        assert "docs/api.md" not in serialized_cache_hit
