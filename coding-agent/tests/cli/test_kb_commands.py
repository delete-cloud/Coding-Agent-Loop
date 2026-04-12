from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from coding_agent.__main__ import main


def _fake_embed(texts: list[str]) -> list[list[float]]:
    return [[float(i)] * 8 for i, _ in enumerate(texts)]


class TestKBIndex:
    def test_kb_index_creates_table(self, tmp_path: Path, monkeypatch):
        doc = tmp_path / "docs"
        doc.mkdir()
        (doc / "readme.md").write_text("# Hello World\nThis is a test document.")

        db_path = tmp_path / "kb_db"

        from coding_agent import kb as kb_module

        original_init = kb_module.KB.__init__

        def patched_init(self_kb, *args, **kwargs):
            kwargs["embedding_fn"] = _fake_embed
            kwargs["embedding_dim"] = 8
            kwargs.setdefault("text_extensions", {".md"})
            original_init(self_kb, *args, **kwargs)

        monkeypatch.setattr(kb_module.KB, "__init__", patched_init)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["kb", "index", str(doc), "--db-path", str(db_path)],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert "done" in result.output.lower() or "indexed" in result.output.lower()

    def test_kb_index_skip_if_table_exists(self, tmp_path: Path, monkeypatch):
        import asyncio

        from coding_agent import kb as kb_module
        from coding_agent.kb import KB

        doc = tmp_path / "docs"
        doc.mkdir()
        (doc / "readme.md").write_text("# Test")

        db_path = tmp_path / "kb_db"
        kb = KB(db_path=db_path, embedding_dim=8, embedding_fn=_fake_embed)
        asyncio.run(kb.index_file(Path("existing.md"), "existing content"))

        original_init = kb_module.KB.__init__

        def patched_init(self_kb, *args, **kwargs):
            kwargs["embedding_fn"] = _fake_embed
            kwargs["embedding_dim"] = 8
            kwargs.setdefault("text_extensions", {".md"})
            original_init(self_kb, *args, **kwargs)

        monkeypatch.setattr(kb_module.KB, "__init__", patched_init)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["kb", "index", str(doc), "--db-path", str(db_path)],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert (
            "already exists" in result.output.lower() or "skip" in result.output.lower()
        )

    def test_kb_index_missing_path_errors(self):
        runner = CliRunner()
        result = runner.invoke(main, ["kb", "index"])

        assert result.exit_code != 0


class TestKBSearch:
    def test_kb_search_returns_results(self, tmp_path: Path, monkeypatch):
        import asyncio

        from coding_agent import kb as kb_module
        from coding_agent.kb import KB

        db_path = tmp_path / "kb_db"
        kb = KB(db_path=db_path, embedding_dim=8, embedding_fn=_fake_embed)
        asyncio.run(
            kb.index_file(Path("test.md"), "Python programming guide with examples")
        )

        original_init = kb_module.KB.__init__

        def patched_init(self_kb, *args, **kwargs):
            kwargs["embedding_fn"] = _fake_embed
            kwargs["embedding_dim"] = 8
            original_init(self_kb, *args, **kwargs)

        monkeypatch.setattr(kb_module.KB, "__init__", patched_init)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["kb", "search", "Python", "--db-path", str(db_path)],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert "test.md" in result.output or "Python" in result.output

    def test_kb_search_no_table_shows_message(self, tmp_path: Path, monkeypatch):
        from coding_agent import kb as kb_module

        db_path = tmp_path / "kb_db"

        original_init = kb_module.KB.__init__

        def patched_init(self_kb, *args, **kwargs):
            kwargs["embedding_fn"] = _fake_embed
            kwargs["embedding_dim"] = 8
            original_init(self_kb, *args, **kwargs)

        monkeypatch.setattr(kb_module.KB, "__init__", patched_init)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["kb", "search", "anything", "--db-path", str(db_path)],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert (
            "no index" in result.output.lower() or "not found" in result.output.lower()
        )

    def test_kb_search_uses_configured_embedding_model(
        self, tmp_path: Path, monkeypatch
    ):
        captured_models: list[str] = []

        from coding_agent import kb as kb_module

        original_init = kb_module.KB.__init__
        original_exists = Path.exists

        def patched_init(self_kb, *args, **kwargs):
            model = kwargs.get("embedding_model")
            if isinstance(model, str):
                captured_models.append(model)
            kwargs["embedding_fn"] = _fake_embed
            kwargs["embedding_dim"] = 8
            original_init(self_kb, *args, **kwargs)

        def patched_exists(path_obj: Path) -> bool:
            if path_obj.name == "agent.toml" and path_obj.parent.name == "coding_agent":
                return True
            return original_exists(path_obj)

        monkeypatch.setattr(kb_module.KB, "__init__", patched_init)
        monkeypatch.setattr(Path, "exists", patched_exists)

        class FakeCfg:
            extra = {
                "kb": {
                    "db_path": "kb",
                    "embedding_model": "text-embedding-3-large",
                    "embedding_dim": 8,
                }
            }

        monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path / "data"))

        from agentkit.config import loader as config_loader

        monkeypatch.setattr(
            config_loader, "load_config", lambda *_args, **_kwargs: FakeCfg()
        )

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["kb", "search", "anything"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert captured_models == ["text-embedding-3-large"]
