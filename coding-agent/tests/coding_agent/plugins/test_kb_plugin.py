from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from coding_agent.kb import KB
from coding_agent.plugins.kb import KBPlugin


def _fake_embed(texts: list[str]) -> list[list[float]]:
    return [[float(i)] * 8 for i, _ in enumerate(texts)]


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
            embedding_dim=8,
            embedding_fn=_fake_embed,
        )

        state = plugin.do_mount()

        assert "kb" in state
        assert "has_table" in state
        assert state["has_table"] is False

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
        assert msg["content"].startswith("[KB]")

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

        def tracking_search(query: str, k: int = 5):
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
