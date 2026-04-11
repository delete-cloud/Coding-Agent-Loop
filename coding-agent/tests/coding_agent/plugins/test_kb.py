import asyncio
from pathlib import Path

from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape

from coding_agent.plugins.kb import KBPlugin


def _fake_embed(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for text in texts:
        lower = text.lower()
        vector = [0.0] * 8
        if "auth" in lower or "jwt" in lower:
            vector[0] = 10.0
        if "api" in lower or "rest" in lower:
            vector[1] = 10.0
        if vector == [0.0] * 8:
            vector[2] = 1.0
        vectors.append(vector)
    return vectors


class TestKBPlugin:
    def test_state_key(self):
        plugin = KBPlugin(
            db_path=Path("/tmp/kb-db"), embedding_dim=8, embedding_fn=_fake_embed
        )
        assert plugin.state_key == "kb"

    def test_hooks(self):
        plugin = KBPlugin(
            db_path=Path("/tmp/kb-db"), embedding_dim=8, embedding_fn=_fake_embed
        )
        hooks = plugin.hooks()
        assert hooks.keys() == {"mount", "build_context"}

    def test_mount_reports_missing_table(self, tmp_path: Path):
        plugin = KBPlugin(
            db_path=tmp_path / "kb-db", embedding_dim=8, embedding_fn=_fake_embed
        )

        state = plugin.do_mount()

        assert state["has_table"] is False
        assert state["kb"] is plugin._kb

    def test_build_context_returns_empty_without_table(self, tmp_path: Path):
        plugin = KBPlugin(
            db_path=tmp_path / "kb-db", embedding_dim=8, embedding_fn=_fake_embed
        )
        plugin.do_mount()
        tape = Tape()
        tape.append(
            Entry(
                kind="message",
                payload={"role": "user", "content": "how does auth work?"},
            )
        )

        result = plugin.build_context(tape=tape)

        assert result == []

    def test_build_context_returns_grounding(self, tmp_path: Path):
        db_path = tmp_path / "kb-db"
        plugin = KBPlugin(db_path=db_path, embedding_dim=8, embedding_fn=_fake_embed)

        from coding_agent.kb import KB

        kb = KB(db_path=db_path, embedding_dim=8, embedding_fn=_fake_embed)
        asyncio.run(
            kb.index_file(
                Path("src/auth.py"), "Authentication module with JWT token validation."
            )
        )
        asyncio.run(
            kb.index_file(
                Path("docs/api.md"), "REST API documentation for the service."
            )
        )

        plugin.do_mount()
        tape = Tape()
        tape.append(
            Entry(
                kind="message",
                payload={"role": "user", "content": "how does auth work?"},
            )
        )

        result = plugin.build_context(tape=tape)

        assert len(result) == 1
        assert result[0]["role"] == "system"
        assert result[0]["content"].startswith("[KB]")
        assert "src/auth.py" in result[0]["content"]
        assert "Authentication module" in result[0]["content"]

    def test_build_context_reuses_cached_result_for_same_message(self, tmp_path: Path):
        db_path = tmp_path / "kb-db"
        plugin = KBPlugin(db_path=db_path, embedding_dim=8, embedding_fn=_fake_embed)

        from coding_agent.kb import KB

        kb = KB(db_path=db_path, embedding_dim=8, embedding_fn=_fake_embed)
        asyncio.run(
            kb.index_file(
                Path("src/auth.py"), "Authentication module with JWT token validation."
            )
        )

        plugin.do_mount()
        assert plugin._kb is not None

        calls = {"count": 0}
        original_search_sync = plugin._kb.search_sync

        def counted_search_sync(query: str, k: int = 5):
            calls["count"] += 1
            return original_search_sync(query, k=k)

        plugin._kb.search_sync = counted_search_sync

        tape = Tape()
        tape.append(
            Entry(
                kind="message",
                payload={"role": "user", "content": "how does auth work?"},
            )
        )

        first = plugin.build_context(tape=tape)
        second = plugin.build_context(tape=tape)

        assert first == second
        assert calls["count"] == 1

    def test_build_context_truncates_long_chunks(self, tmp_path: Path):
        db_path = tmp_path / "kb-db"
        plugin = KBPlugin(db_path=db_path, embedding_dim=8, embedding_fn=_fake_embed)

        from coding_agent.kb import KB

        kb = KB(db_path=db_path, embedding_dim=8, embedding_fn=_fake_embed)
        asyncio.run(kb.index_file(Path("docs/auth.md"), "auth " + ("A" * 2000)))

        plugin.do_mount()
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "auth"}))

        result = plugin.build_context(tape=tape)
        content = result[0]["content"]
        lines = content.splitlines()
        chunk_line = lines[3]

        assert chunk_line.startswith("auth ")
        assert len(chunk_line) == 503
        assert chunk_line.endswith("...")
