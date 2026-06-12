"""Tests for knowledge base RAG module."""

import hashlib
import json
import sys
import tempfile
import types
from pathlib import Path
from typing import Any

import pytest

from coding_agent import kb as kb_module
from coding_agent.kb import DocumentChunk, KB, KBSearchResult


class MockEmbeddingFn:
    """Mock embedding function for testing."""

    def __init__(self, dimension: int = 1536):
        self.dimension = dimension
        self.call_count = 0
        self.last_texts: list[str] = []

    def __call__(self, texts: list[str]) -> list[list[float]]:
        """Generate deterministic embeddings based on content hash."""
        self.call_count += 1
        self.last_texts = texts

        embeddings = []
        for text in texts:
            # Create deterministic embedding based on text content
            import hashlib

            hash_val = int(hashlib.md5(text.encode()).hexdigest(), 16)

            # Generate a vector with some structure
            embedding = []
            for i in range(self.dimension):
                # Use hash to create pseudo-random but deterministic values
                val = ((hash_val + i * 997) % 10000) / 10000.0
                # Normalize to make search more realistic
                embedding.append(val)

            # Normalize the vector
            norm = sum(x * x for x in embedding) ** 0.5
            if norm > 0:
                embedding = [x / norm for x in embedding]

            embeddings.append(embedding)

        return embeddings


@pytest.fixture
def temp_db_path():
    """Create a temporary directory for the test database."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test_kb.lancedb"


@pytest.fixture
def mock_embedding_fn():
    """Create a mock embedding function."""
    return MockEmbeddingFn()


def _repo_retrieval_embed(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for text in texts:
        lower = text.lower()
        if "auth" in lower or "jwt" in lower:
            vectors.append([1.0, 0.0, 0.0, 0.0])
        elif "billing" in lower or "invoice" in lower:
            vectors.append([0.0, 1.0, 0.0, 0.0])
        else:
            vectors.append([0.0, 0.0, 1.0, 0.0])
    return vectors


def _failure_retrieval_embed(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for text in texts:
        lower = text.lower()
        if "auth" in lower or "expired token" in lower:
            vectors.append([1.0, 0.0, 0.0, 0.0])
        elif "billing" in lower or "invoice" in lower:
            vectors.append([0.0, 1.0, 0.0, 0.0])
        else:
            vectors.append([0.0, 0.0, 1.0, 0.0])
    return vectors


def _context_fixture(name: str) -> str:
    return (Path(__file__).parent / "fixtures" / "context_system" / name).read_text()


@pytest.fixture
async def kb(temp_db_path, mock_embedding_fn):
    """Create a KB instance with mock embeddings."""
    kb_instance = KB(
        db_path=temp_db_path,
        embedding_fn=mock_embedding_fn,
        chunk_size=100,  # Small chunks for testing
        chunk_overlap=20,
    )
    yield kb_instance


class TestDocumentChunk:
    """Tests for DocumentChunk dataclass."""

    def test_document_chunk_creation(self):
        """Test creating a DocumentChunk."""
        chunk = DocumentChunk(
            id="test-id-123",
            content="Test content here",
            source="/path/to/file.py",
            metadata={"key": "value"},
        )

        assert chunk.id == "test-id-123"
        assert chunk.content == "Test content here"
        assert chunk.source == "/path/to/file.py"
        assert chunk.metadata == {"key": "value"}


class TestKBInitialization:
    """Tests for KB initialization."""

    def test_kb_init_with_defaults(self, temp_db_path):
        """Test KB initialization with default parameters."""
        kb = KB(db_path=temp_db_path)

        assert kb.db_path == temp_db_path
        assert kb.embedding_model == "text-embedding-3-small"
        assert kb.embedding_base_url is None
        assert kb.chunk_size == 1200
        assert kb.chunk_overlap == 200
        assert kb._embedding_fn is None

    def test_kb_init_with_custom_params(self, temp_db_path, mock_embedding_fn):
        """Test KB initialization with custom parameters."""
        kb = KB(
            db_path=temp_db_path,
            embedding_model="text-embedding-3-large",
            embedding_base_url="https://embed.example/v1",
            chunk_size=500,
            chunk_overlap=50,
            embedding_fn=mock_embedding_fn,
        )

        assert kb.db_path == temp_db_path
        assert kb.embedding_model == "text-embedding-3-large"
        assert kb.embedding_base_url == "https://embed.example/v1"
        assert kb.chunk_size == 500
        assert kb.chunk_overlap == 50
        assert kb._embedding_fn is mock_embedding_fn

    def test_openai_clients_use_embedding_base_url(self, temp_db_path, monkeypatch):
        async_calls: list[dict[str, Any]] = []
        sync_calls: list[dict[str, Any]] = []

        class FakeAsyncOpenAI:
            def __init__(self, **kwargs: Any) -> None:
                async_calls.append(kwargs)

        class FakeOpenAI:
            def __init__(self, **kwargs: Any) -> None:
                sync_calls.append(kwargs)

        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setitem(
            sys.modules,
            "openai",
            types.SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI, OpenAI=FakeOpenAI),
        )
        kb = KB(
            db_path=temp_db_path,
            embedding_base_url="https://embed.example/v1",
        )

        kb._get_openai_client()
        kb._get_openai_sync_client()

        assert async_calls == [
            {"api_key": "sk-test", "base_url": "https://embed.example/v1"}
        ]
        assert sync_calls == [
            {"api_key": "sk-test", "base_url": "https://embed.example/v1"}
        ]

    def test_kb_creates_directory(self, temp_db_path):
        """Test that KB creates the database directory."""
        db_path = temp_db_path / "nested" / "path"
        assert not db_path.parent.exists()

        KB(db_path=db_path)

        assert db_path.parent.exists()


class TestKBIndexing:
    """Tests for document indexing."""

    @pytest.mark.asyncio
    async def test_index_file_single_chunk(self, temp_db_path, mock_embedding_fn):
        """Test indexing a small file that fits in one chunk."""
        kb = KB(
            db_path=temp_db_path,
            embedding_fn=mock_embedding_fn,
            chunk_size=1000,
            chunk_overlap=100,
        )

        content = "This is a short file content."
        await kb.index_file(Path("/test/file.py"), content)

        # Verify embedding was called
        assert mock_embedding_fn.call_count == 1
        assert len(mock_embedding_fn.last_texts) == 1
        assert mock_embedding_fn.last_texts[0] == content

    @pytest.mark.asyncio
    async def test_index_file_multiple_chunks(self, temp_db_path, mock_embedding_fn):
        """Test indexing a file that requires multiple chunks."""
        kb = KB(
            db_path=temp_db_path,
            embedding_fn=mock_embedding_fn,
            chunk_size=10,  # Very small for testing
            chunk_overlap=2,
        )

        # Create content that will be split into multiple chunks
        # Each chunk ~40 chars (10 tokens * 4 chars), overlap ~8 chars
        content = "A" * 100
        await kb.index_file(Path("/test/file.py"), content)

        # Should have multiple chunks
        assert mock_embedding_fn.call_count == 1
        assert len(mock_embedding_fn.last_texts) > 1

    @pytest.mark.asyncio
    async def test_index_file_empty_content(self, temp_db_path, mock_embedding_fn):
        """Test indexing a file with empty content."""
        kb = KB(
            db_path=temp_db_path,
            embedding_fn=mock_embedding_fn,
        )

        await kb.index_file(Path("/test/empty.py"), "")

        # Should not call embedding for empty content
        assert mock_embedding_fn.call_count == 0

    @pytest.mark.asyncio
    async def test_repo_chunk_metadata_records_source_kind_and_repo_path(
        self, tmp_path, mock_embedding_fn
    ):
        """Repo-aware indexing records stable source and chunk metadata."""
        repo_root = tmp_path / "repo"
        source_path = repo_root / "src" / "app.py"
        source_path.parent.mkdir(parents=True)
        content = "def alpha():\n    return 'alpha'\n"
        source_path.write_text(content)
        kb = KB(
            db_path=tmp_path / "kb_db",
            embedding_fn=mock_embedding_fn,
            chunk_size=1000,
            chunk_overlap=0,
        )

        await kb.index_file(source_path, content, repo_root=repo_root)

        results = await kb.search("alpha", k=1)
        assert len(results) == 1
        metadata = results[0].chunk.metadata
        assert metadata["metadata_version"] == 1
        assert metadata["source_kind"] == "repo_file"
        assert metadata["repo_path"] == "src/app.py"
        assert metadata["language"] == "python"
        assert metadata["line_start"] == 1
        assert metadata["line_end"] == 2
        assert (
            metadata["document_sha256"]
            == hashlib.sha256(content.encode("utf-8")).hexdigest()
        )
        assert (
            metadata["chunk_sha256"]
            == hashlib.sha256(results[0].chunk.content.encode("utf-8")).hexdigest()
        )
        assert isinstance(metadata["source_id"], str)
        assert len(metadata["source_id"]) == 64

    @pytest.mark.asyncio
    async def test_index_directory_records_repo_relative_metadata(
        self, tmp_path, mock_embedding_fn
    ):
        repo_root = tmp_path / "repo"
        source_path = repo_root / "src" / "app.py"
        source_path.parent.mkdir(parents=True)
        source_path.write_text("def alpha():\n    return 'alpha'\n")
        kb = KB(
            db_path=tmp_path / "kb_db",
            embedding_fn=mock_embedding_fn,
            chunk_size=1000,
            chunk_overlap=0,
        )

        await kb.index_directory(repo_root, show_progress=False)

        results = await kb.search("alpha", k=1)
        assert len(results) == 1
        assert results[0].chunk.metadata["source_kind"] == "repo_file"
        assert results[0].chunk.metadata["repo_path"] == "src/app.py"

    @pytest.mark.asyncio
    async def test_index_file_rejects_path_outside_repo_root(
        self, tmp_path, mock_embedding_fn
    ):
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        outside = tmp_path / "outside.py"
        outside.write_text("print('outside')\n")
        kb = KB(db_path=tmp_path / "kb_db", embedding_fn=mock_embedding_fn)

        with pytest.raises(ValueError, match="outside repo_root"):
            await kb.index_file(
                outside,
                outside.read_text(),
                repo_root=repo_root,
            )
        assert mock_embedding_fn.call_count == 0
        assert not kb.has_table()

    @pytest.mark.asyncio
    async def test_index_file_rejects_embedding_count_mismatch(self, tmp_path):
        def short_embed(texts: list[str]) -> list[list[float]]:
            return [[0.1] * 1536 for _ in texts[:-1]]

        kb = KB(
            db_path=tmp_path / "kb_db",
            embedding_fn=short_embed,
            chunk_size=1,
            chunk_overlap=0,
        )

        with pytest.raises(ValueError, match="shorter than argument 1"):
            await kb.index_file(Path("src/app.py"), "abcdefghijkl")

    @pytest.mark.asyncio
    async def test_repo_source_id_stays_stable_when_document_content_changes(
        self, tmp_path, mock_embedding_fn
    ):
        repo_root = tmp_path / "repo"
        source_path = repo_root / "src" / "app.py"
        source_path.parent.mkdir(parents=True)
        first_content = "def alpha():\n    return 'first'\n"
        second_content = "def alpha():\n    return 'second'\n"
        kb = KB(
            db_path=tmp_path / "kb_db",
            embedding_fn=mock_embedding_fn,
            chunk_size=1000,
            chunk_overlap=0,
        )

        await kb.index_file(source_path, first_content, repo_root=repo_root)
        await kb.index_file(source_path, second_content, repo_root=repo_root)

        rows = kb._get_table().to_arrow().to_pylist()
        metadata = [json.loads(row["metadata"]) for row in rows]
        assert len(metadata) == 2
        assert {item["repo_path"] for item in metadata} == {"src/app.py"}
        assert len({item["source_id"] for item in metadata}) == 1
        assert {item["document_sha256"] for item in metadata} == {
            hashlib.sha256(first_content.encode("utf-8")).hexdigest(),
            hashlib.sha256(second_content.encode("utf-8")).hexdigest(),
        }

    @pytest.mark.asyncio
    async def test_index_directory_records_symlink_repo_path(
        self, tmp_path, mock_embedding_fn
    ):
        repo_root = tmp_path / "repo"
        target_path = repo_root / "src" / "target.py"
        link_path = repo_root / "docs" / "linked.py"
        target_path.parent.mkdir(parents=True)
        link_path.parent.mkdir(parents=True)
        target_path.write_text("def target():\n    return 'target'\n")
        try:
            link_path.symlink_to(target_path)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks are not supported on this filesystem")
        kb = KB(
            db_path=tmp_path / "kb_db",
            embedding_fn=mock_embedding_fn,
            chunk_size=1000,
            chunk_overlap=0,
        )

        await kb.index_directory(repo_root, show_progress=False)

        rows = kb._get_table().to_arrow().to_pylist()
        metadata = [json.loads(row["metadata"]) for row in rows]
        assert {item["repo_path"] for item in metadata} == {
            "docs/linked.py",
            "src/target.py",
        }

    @pytest.mark.asyncio
    async def test_index_directory_skips_symlink_targets_outside_repo(
        self, tmp_path, mock_embedding_fn
    ):
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        inside_path = repo_root / "inside.py"
        outside_path = tmp_path / "outside.py"
        link_path = repo_root / "external.py"
        inside_path.write_text("print('inside')\n")
        outside_path.write_text("print('outside')\n")
        try:
            link_path.symlink_to(outside_path)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks are not supported on this filesystem")
        kb = KB(
            db_path=tmp_path / "kb_db",
            embedding_fn=mock_embedding_fn,
            chunk_size=1000,
            chunk_overlap=0,
        )

        await kb.index_directory(repo_root, show_progress=False)

        rows = kb._get_table().to_arrow().to_pylist()
        metadata = [json.loads(row["metadata"]) for row in rows]
        assert [item["repo_path"] for item in metadata] == ["inside.py"]

    @pytest.mark.asyncio
    async def test_index_file_deterministic_ids(self, temp_db_path, mock_embedding_fn):
        """Test that indexing the same file produces consistent IDs."""
        kb = KB(
            db_path=temp_db_path,
            embedding_fn=mock_embedding_fn,
            chunk_size=50,
            chunk_overlap=10,
        )

        content = "This is test content for deterministic ID generation."

        # Index the same content twice
        await kb.index_file(Path("/test/file.py"), content)
        first_call_texts = mock_embedding_fn.last_texts.copy()

        # Get table to check IDs
        table = kb._get_table()

        first_count = len(table.to_arrow().to_pandas())

        await kb.index_file(Path("/test/file.py"), content)

        # Should index the same content (no deduplication in current implementation)
        second_count = len(table.to_pandas())
        assert second_count > first_count

        # But the chunking should be the same
        assert mock_embedding_fn.last_texts == first_call_texts

    @pytest.mark.asyncio
    async def test_index_directory(self, temp_db_path, mock_embedding_fn):
        """Test indexing a directory of files."""
        kb = KB(
            db_path=temp_db_path,
            embedding_fn=mock_embedding_fn,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            # Create test files
            (root / "test.py").write_text("def hello(): pass")
            (root / "readme.md").write_text("# README\nThis is a test.")
            (root / "data.txt").write_text("Some text data")
            (root / "binary.bin").write_bytes(b"\x00\x01\x02")  # Should be skipped

            await kb.index_directory(root)

        # Should have indexed the text files
        table = kb._get_table()

        df = table.to_arrow().to_pandas()
        sources = df["source"].tolist()

        assert any("test.py" in s for s in sources)
        assert any("readme.md" in s for s in sources)
        assert any("data.txt" in s for s in sources)
        assert not any("binary.bin" in s for s in sources)

    @pytest.mark.asyncio
    async def test_index_directory_nested(self, temp_db_path, mock_embedding_fn):
        """Test indexing nested directories."""
        kb = KB(
            db_path=temp_db_path,
            embedding_fn=mock_embedding_fn,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            # Create nested structure
            (root / "src" / "utils").mkdir(parents=True)
            (root / "src" / "main.py").write_text("print('main')")
            (root / "src" / "utils" / "helpers.py").write_text("def helper(): pass")

            await kb.index_directory(root)

        table = kb._get_table()

        df = table.to_arrow().to_pandas()
        sources = df["source"].tolist()

        assert any("main.py" in s for s in sources)
        assert any("helpers.py" in s for s in sources)


class TestKBSearch:
    """Tests for KB search functionality."""

    @pytest.mark.asyncio
    async def test_search_basic(self, temp_db_path, mock_embedding_fn):
        """Test basic vector search."""
        kb = KB(
            db_path=temp_db_path,
            embedding_fn=mock_embedding_fn,
            chunk_size=100,
        )

        # Index some content
        await kb.index_file(Path("/test/file.py"), "This is about Python programming.")
        await kb.index_file(Path("/test/other.py"), "This is about JavaScript coding.")

        # Search
        results = await kb.search("Python programming", k=2)

        assert len(results) > 0
        assert all(isinstance(r, KBSearchResult) for r in results)
        assert all(hasattr(r, "chunk") for r in results)
        assert all(hasattr(r, "score") for r in results)

    @pytest.mark.asyncio
    async def test_search_returns_correct_structure(
        self, temp_db_path, mock_embedding_fn
    ):
        """Test that search returns properly structured results."""
        kb = KB(
            db_path=temp_db_path,
            embedding_fn=mock_embedding_fn,
            chunk_size=50,
        )

        await kb.index_file(Path("/test/source.py"), "Test content for search.")

        results = await kb.search("test content", k=5)

        for result in results:
            assert isinstance(result.chunk, DocumentChunk)
            assert isinstance(result.chunk.id, str)
            assert isinstance(result.chunk.content, str)
            assert isinstance(result.chunk.source, str)
            assert isinstance(result.chunk.metadata, dict)
            assert isinstance(result.score, float)

    @pytest.mark.asyncio
    async def test_search_empty_query(self, temp_db_path, mock_embedding_fn):
        """Test search with empty query."""
        kb = KB(
            db_path=temp_db_path,
            embedding_fn=mock_embedding_fn,
        )

        # Index some content first
        await kb.index_file(Path("/test/file.py"), "Some content")

        # Search with empty query
        results = await kb.search("")

        assert results == []

    @pytest.mark.asyncio
    async def test_search_whitespace_query(self, temp_db_path, mock_embedding_fn):
        """Test search with whitespace-only query."""
        kb = KB(
            db_path=temp_db_path,
            embedding_fn=mock_embedding_fn,
        )

        # Index some content first
        await kb.index_file(Path("/test/file.py"), "Some content")

        # Search with whitespace query
        results = await kb.search("   \n\t  ")

        assert results == []

    @pytest.mark.asyncio
    async def test_search_limit_k(self, temp_db_path, mock_embedding_fn):
        """Test that k parameter limits results."""
        kb = KB(
            db_path=temp_db_path,
            embedding_fn=mock_embedding_fn,
            chunk_size=20,
        )

        # Index multiple chunks
        content = " ".join([f"Section {i} with unique content." for i in range(10)])
        await kb.index_file(Path("/test/file.py"), content)

        # Search with different k values
        results_k2 = await kb.search("section", k=2)
        results_k5 = await kb.search("section", k=5)

        assert len(results_k2) <= 2
        assert len(results_k5) <= 5

    @pytest.mark.asyncio
    async def test_search_no_results(self, temp_db_path, mock_embedding_fn):
        """Test search when no content is indexed."""
        kb = KB(
            db_path=temp_db_path,
            embedding_fn=mock_embedding_fn,
        )

        # Don't index anything
        results = await kb.search("anything")

        assert results == []

    @pytest.mark.asyncio
    async def test_repo_retrieval_returns_ranked_evidence_with_fake_embedder(
        self, tmp_path
    ):
        repo_root = tmp_path / "repo"
        auth_path = repo_root / "src" / "auth.py"
        billing_path = repo_root / "src" / "billing.py"
        auth_content = "def validate_jwt():\n    return 'auth token'\n"
        billing_content = "def invoice_total():\n    return 'billing invoice'\n"
        auth_path.parent.mkdir(parents=True)
        auth_path.write_text(auth_content)
        billing_path.write_text(billing_content)
        kb = KB(
            db_path=tmp_path / "kb_db",
            embedding_dim=4,
            embedding_fn=_repo_retrieval_embed,
            chunk_size=1000,
            chunk_overlap=0,
        )
        await kb.index_directory(repo_root, show_progress=False)

        results = await kb.search_repo("jwt auth", k=2)

        assert [result.rank for result in results] == [1, 2]
        assert [result.repo_path for result in results] == [
            "src/auth.py",
            "src/billing.py",
        ]
        assert results[0].source_kind == "repo_file"
        assert results[0].source_id == results[0].chunk.metadata["source_id"]
        assert results[0].chunk_id == results[0].chunk.id
        assert results[0].language == "python"
        assert results[0].line_start == 1
        assert results[0].line_end == 2
        assert (
            results[0].document_sha256
            == hashlib.sha256(auth_content.encode("utf-8")).hexdigest()
        )
        assert (
            results[0].chunk_sha256
            == hashlib.sha256(results[0].chunk.content.encode("utf-8")).hexdigest()
        )
        assert results[0].score <= results[1].score

    @pytest.mark.asyncio
    async def test_repo_retrieval_skips_legacy_rows_without_repo_metadata(
        self, tmp_path
    ):
        repo_root = tmp_path / "repo"
        repo_path = repo_root / "src" / "auth.py"
        repo_path.parent.mkdir(parents=True)
        repo_path.write_text("def auth_guard():\n    return 'repo auth'\n")
        kb = KB(
            db_path=tmp_path / "kb_db",
            embedding_dim=4,
            embedding_fn=_repo_retrieval_embed,
            chunk_size=1000,
            chunk_overlap=0,
        )
        table = kb._get_table()
        table.add(
            [
                {
                    "id": "legacy-auth",
                    "content": "legacy auth row",
                    "source": "/old/auth.py",
                    "metadata": json.dumps({"chunk_index": 0}),
                    "vector": [1.0, 0.0, 0.0, 0.0],
                }
            ]
        )
        await kb.index_directory(repo_root, show_progress=False)

        results = await kb.search_repo("auth", k=1)

        assert len(results) == 1
        assert results[0].repo_path == "src/auth.py"
        assert results[0].chunk.id != "legacy-auth"

    @pytest.mark.asyncio
    async def test_repo_retrieval_expands_past_legacy_candidate_window(self, tmp_path):
        def mixed_legacy_embed(texts: list[str]) -> list[list[float]]:
            vectors: list[list[float]] = []
            for text in texts:
                if text == "auth":
                    vectors.append([1.0, 0.0, 0.0, 0.0])
                elif "repo evidence" in text:
                    vectors.append([0.95, 0.0, 0.0, 0.0])
                else:
                    vectors.append([0.0, 0.0, 1.0, 0.0])
            return vectors

        repo_root = tmp_path / "repo"
        repo_path = repo_root / "src" / "auth.py"
        repo_path.parent.mkdir(parents=True)
        repo_path.write_text("repo evidence for authentication\n")
        kb = KB(
            db_path=tmp_path / "kb_db",
            embedding_dim=4,
            embedding_fn=mixed_legacy_embed,
            chunk_size=1000,
            chunk_overlap=0,
        )
        table = kb._get_table()
        table.add(
            [
                {
                    "id": f"legacy-auth-{i}",
                    "content": f"legacy auth row {i}",
                    "source": f"/old/auth_{i}.py",
                    "metadata": json.dumps({"chunk_index": i}),
                    "vector": [1.0, 0.0, 0.0, 0.0],
                }
                for i in range(4)
            ]
        )
        await kb.index_directory(repo_root, show_progress=False)

        results = await kb.search_repo("auth", k=1)

        assert len(results) == 1
        assert results[0].repo_path == "src/auth.py"
        assert results[0].chunk.id not in {f"legacy-auth-{i}" for i in range(4)}

    @pytest.mark.asyncio
    async def test_repo_retrieval_stops_at_candidate_fetch_cap(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(kb_module, "_MAX_REPO_RETRIEVAL_FETCH_K", 4, raising=False)

        def mixed_legacy_embed(texts: list[str]) -> list[list[float]]:
            vectors: list[list[float]] = []
            for text in texts:
                if text == "auth":
                    vectors.append([1.0, 0.0, 0.0, 0.0])
                elif "repo evidence" in text:
                    vectors.append([0.95, 0.0, 0.0, 0.0])
                else:
                    vectors.append([0.0, 0.0, 1.0, 0.0])
            return vectors

        repo_root = tmp_path / "repo"
        repo_path = repo_root / "src" / "auth.py"
        repo_path.parent.mkdir(parents=True)
        repo_path.write_text("repo evidence for authentication\n")
        kb = KB(
            db_path=tmp_path / "kb_db",
            embedding_dim=4,
            embedding_fn=mixed_legacy_embed,
            chunk_size=1000,
            chunk_overlap=0,
        )
        table = kb._get_table()
        table.add(
            [
                {
                    "id": f"legacy-auth-{i}",
                    "content": f"legacy auth row {i}",
                    "source": f"/old/auth_{i}.py",
                    "metadata": json.dumps({"chunk_index": i}),
                    "vector": [1.0, 0.0, 0.0, 0.0],
                }
                for i in range(4)
            ]
        )
        await kb.index_directory(repo_root, show_progress=False)

        results = await kb.search_repo("auth", k=1)

        assert results == []

    @pytest.mark.asyncio
    async def test_repo_retrieval_rejects_k_above_candidate_fetch_cap(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(kb_module, "_MAX_REPO_RETRIEVAL_FETCH_K", 4, raising=False)
        kb = KB(
            db_path=tmp_path / "kb_db",
            embedding_dim=4,
            embedding_fn=_repo_retrieval_embed,
        )

        with pytest.raises(ValueError, match="k must be less than or equal to"):
            await kb.search_repo("auth", k=5)

    @pytest.mark.asyncio
    async def test_failure_retrieval_indexes_pytest_failure_evidence(self, tmp_path):
        auth_failure = _context_fixture("pytest_auth_failure.txt")
        billing_failure = _context_fixture("pytest_billing_failure.txt")
        kb = KB(
            db_path=tmp_path / "kb_db",
            embedding_dim=4,
            embedding_fn=_failure_retrieval_embed,
        )

        await kb.index_test_failure(
            command_label="uv run pytest tests/test_auth.py::test_rejects_expired_token",
            exit_code=1,
            test_node_id="tests/test_auth.py::test_rejects_expired_token",
            repo_path="tests/test_auth.py",
            line_start=18,
            line_end=18,
            failure_snippet=auth_failure,
        )
        await kb.index_test_failure(
            command_label="uv run pytest tests/test_billing.py::test_invoice_total_rounds_cents",
            exit_code=1,
            test_node_id="tests/test_billing.py::test_invoice_total_rounds_cents",
            repo_path="tests/test_billing.py",
            line_start=44,
            line_end=44,
            failure_snippet=billing_failure,
        )

        results = await kb.search_test_failures("expired auth assertion", k=2)

        assert [result.rank for result in results] == [1, 2]
        assert [result.test_node_id for result in results] == [
            "tests/test_auth.py::test_rejects_expired_token",
            "tests/test_billing.py::test_invoice_total_rounds_cents",
        ]
        assert results[0].source_kind == "test_failure"
        assert results[0].chunk_id == results[0].chunk.id
        assert results[0].source_id == results[0].chunk.metadata["source_id"]
        assert results[0].command_label == (
            "uv run pytest tests/test_auth.py::test_rejects_expired_token"
        )
        assert results[0].exit_code == 1
        assert results[0].repo_path == "tests/test_auth.py"
        assert results[0].line_start == 18
        assert results[0].line_end == 18
        assert results[0].chunk.content == auth_failure
        assert (
            results[0].failure_sha256
            == hashlib.sha256(auth_failure.encode("utf-8")).hexdigest()
        )
        assert (
            results[0].snippet_sha256
            == hashlib.sha256(auth_failure.encode("utf-8")).hexdigest()
        )
        assert results[0].score <= results[1].score

    @pytest.mark.asyncio
    async def test_failure_retrieval_skips_non_failure_rows(self, tmp_path):
        def mixed_failure_embed(texts: list[str]) -> list[list[float]]:
            vectors: list[list[float]] = []
            for text in texts:
                if text == "auth":
                    vectors.append([1.0, 0.0, 0.0, 0.0])
                elif "pytest failure evidence" in text:
                    vectors.append([0.95, 0.0, 0.0, 0.0])
                else:
                    vectors.append([0.0, 0.0, 1.0, 0.0])
            return vectors

        auth_failure = "pytest failure evidence for auth regression\n"
        kb = KB(
            db_path=tmp_path / "kb_db",
            embedding_dim=4,
            embedding_fn=mixed_failure_embed,
        )
        table = kb._get_table()
        table.add(
            [
                {
                    "id": f"repo-auth-{i}",
                    "content": f"repo auth row {i}",
                    "source": f"src/auth_{i}.py",
                    "metadata": json.dumps(
                        {
                            "source_kind": "repo_file",
                            "source_id": f"repo-source-{i}",
                            "repo_path": f"src/auth_{i}.py",
                        }
                    ),
                    "vector": [1.0, 0.0, 0.0, 0.0],
                }
                for i in range(4)
            ]
        )
        await kb.index_test_failure(
            command_label="uv run pytest tests/test_auth.py",
            exit_code=1,
            test_node_id="tests/test_auth.py::test_failure_fixture",
            repo_path="tests/test_auth.py",
            line_start=20,
            line_end=20,
            failure_snippet=auth_failure,
        )

        results = await kb.search_test_failures("auth", k=1)

        assert len(results) == 1
        assert results[0].test_node_id == "tests/test_auth.py::test_failure_fixture"
        assert results[0].chunk.id not in {f"repo-auth-{i}" for i in range(4)}

    @pytest.mark.asyncio
    async def test_failure_retrieval_upserts_same_failure_evidence(self, tmp_path):
        auth_failure = _context_fixture("pytest_auth_failure.txt")
        kb = KB(
            db_path=tmp_path / "kb_db",
            embedding_dim=4,
            embedding_fn=_failure_retrieval_embed,
        )
        kwargs = {
            "command_label": "uv run pytest tests/test_auth.py::test_rejects_expired_token",
            "exit_code": 1,
            "test_node_id": "tests/test_auth.py::test_rejects_expired_token",
            "repo_path": "tests/test_auth.py",
            "line_start": 18,
            "line_end": 18,
            "failure_snippet": auth_failure,
        }

        await kb.index_test_failure(**kwargs)
        await kb.index_test_failure(**kwargs)

        results = await kb.search_test_failures("expired auth assertion", k=5)

        assert len(results) == 1
        assert results[0].test_node_id == (
            "tests/test_auth.py::test_rejects_expired_token"
        )


class TestKBHybridSearch:
    """Tests for hybrid search functionality."""

    @pytest.mark.asyncio
    async def test_hybrid_search_basic(self, temp_db_path, mock_embedding_fn):
        """Test basic hybrid search."""
        kb = KB(
            db_path=temp_db_path,
            embedding_fn=mock_embedding_fn,
            chunk_size=100,
        )

        # Index content
        await kb.index_file(
            Path("/test/file.py"), "Python programming guide and tutorial."
        )
        await kb.index_file(
            Path("/test/other.py"), "JavaScript development documentation."
        )

        # Hybrid search
        results = await kb.hybrid_search("Python guide", k=2)

        assert len(results) > 0
        assert all(isinstance(r, KBSearchResult) for r in results)

    @pytest.mark.asyncio
    async def test_hybrid_search_empty_query(self, temp_db_path, mock_embedding_fn):
        """Test hybrid search with empty query."""
        kb = KB(
            db_path=temp_db_path,
            embedding_fn=mock_embedding_fn,
        )

        await kb.index_file(Path("/test/file.py"), "Some content")

        results = await kb.hybrid_search("")

        assert results == []

    @pytest.mark.asyncio
    async def test_hybrid_search_deduplication(self, temp_db_path, mock_embedding_fn):
        """Test that hybrid search deduplicates results."""
        kb = KB(
            db_path=temp_db_path,
            embedding_fn=mock_embedding_fn,
            chunk_size=100,
        )

        # Index content
        await kb.index_file(Path("/test/file.py"), "Python programming best practices.")

        # Hybrid search
        results = await kb.hybrid_search("Python programming", k=5)

        # Check no duplicate IDs
        ids = [r.chunk.id for r in results]
        assert len(ids) == len(set(ids))

    @pytest.mark.asyncio
    async def test_hybrid_search_returns_sorted(self, temp_db_path, mock_embedding_fn):
        """Test that hybrid search results are sorted by score."""
        kb = KB(
            db_path=temp_db_path,
            embedding_fn=mock_embedding_fn,
            chunk_size=50,
        )

        # Index multiple chunks
        await kb.index_file(Path("/test/a.py"), "Python code and algorithms.")
        await kb.index_file(Path("/test/b.py"), "Python data structures.")
        await kb.index_file(Path("/test/c.py"), "JavaScript web development.")

        # Hybrid search
        results = await kb.hybrid_search("Python algorithms", k=5)

        if len(results) > 1:
            # Results should be sorted by score (ascending)
            scores = [r.score for r in results]
            assert scores == sorted(scores)

    @pytest.mark.asyncio
    async def test_hybrid_search_normalizes_fts_and_vector_scores(self, temp_db_path):
        kb = KB(
            db_path=temp_db_path, embedding_fn=lambda texts: [[1.0] * 8 for _ in texts]
        )

        class FakeSearch:
            def __init__(self, results):
                self._results = results

            def limit(self, k):
                return self

            def to_list(self):
                return self._results

        class FakeTable:
            def search(self, value, query_type=None):
                if query_type == "fts":
                    return FakeSearch(
                        [
                            {
                                "id": "fts-1",
                                "content": "fts result",
                                "source": "fts.md",
                                "metadata": "{}",
                                "_score": 12.0,
                            }
                        ]
                    )
                return FakeSearch(
                    [
                        {
                            "id": "vec-1",
                            "content": "vector result",
                            "source": "vec.md",
                            "metadata": "{}",
                            "_distance": 0.6,
                        }
                    ]
                )

        kb._table = FakeTable()

        results = await kb.hybrid_search("query", k=2)

        assert [result.chunk.id for result in results] == ["fts-1", "vec-1"]


class TestChunking:
    """Tests for text chunking functionality."""

    def test_chunk_text_small_content(self, temp_db_path):
        """Test chunking content smaller than chunk size."""
        kb = KB(
            db_path=temp_db_path,
            chunk_size=100,
            chunk_overlap=20,
        )

        content = "Small content"
        chunks = kb._chunk_text(content)

        assert len(chunks) == 1
        assert chunks[0] == content

    def test_chunk_text_large_content(self, temp_db_path):
        """Test chunking content larger than chunk size."""
        kb = KB(
            db_path=temp_db_path,
            chunk_size=10,  # 10 tokens = ~40 chars
            chunk_overlap=2,  # 2 tokens = ~8 chars
        )

        content = "A" * 200
        chunks = kb._chunk_text(content)

        assert len(chunks) > 1

        # Verify overlap
        for i in range(len(chunks) - 1):
            # Each chunk should overlap with the next
            assert len(chunks[i]) > 0
            assert len(chunks[i + 1]) > 0

    def test_chunk_text_exact_size(self, temp_db_path):
        """Test chunking content exactly at chunk size."""
        kb = KB(
            db_path=temp_db_path,
            chunk_size=10,
            chunk_overlap=2,
        )

        # Exactly 10 tokens = ~40 chars
        content = "A" * 40
        chunks = kb._chunk_text(content)

        assert len(chunks) == 1
        assert chunks[0] == content


class TestEmbedding:
    """Tests for embedding functionality."""

    @pytest.mark.asyncio
    async def test_embed_with_mock_function(self, temp_db_path, mock_embedding_fn):
        """Test embedding with mock function."""
        kb = KB(
            db_path=temp_db_path,
            embedding_fn=mock_embedding_fn,
        )

        texts = ["Hello", "World"]
        embeddings = await kb._embed(texts)

        assert len(embeddings) == 2
        assert all(len(e) == 1536 for e in embeddings)
        assert mock_embedding_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_embed_deterministic(self, temp_db_path, mock_embedding_fn):
        """Test that embeddings are deterministic."""
        kb = KB(
            db_path=temp_db_path,
            embedding_fn=mock_embedding_fn,
        )

        text = "Test text"
        embedding1 = await kb._embed([text])
        embedding2 = await kb._embed([text])

        assert embedding1 == embedding2

    @pytest.mark.asyncio
    async def test_embed_normalization(self, temp_db_path, mock_embedding_fn):
        """Test that mock embeddings are normalized."""
        kb = KB(
            db_path=temp_db_path,
            embedding_fn=mock_embedding_fn,
        )

        embeddings = await kb._embed(["Test"])
        embedding = embeddings[0]

        # Check L2 norm is approximately 1
        norm = sum(x * x for x in embedding) ** 0.5
        assert abs(norm - 1.0) < 0.001


class TestKBIntegration:
    """Integration tests for KB."""

    @pytest.mark.asyncio
    async def test_full_workflow(self, temp_db_path, mock_embedding_fn):
        """Test full indexing and search workflow."""
        kb = KB(
            db_path=temp_db_path,
            embedding_fn=mock_embedding_fn,
            chunk_size=50,
            chunk_overlap=10,
        )

        # Index files
        await kb.index_file(
            Path("/docs/python.md"),
            "Python is a high-level programming language. It supports multiple paradigms.",
        )
        await kb.index_file(
            Path("/docs/js.md"),
            "JavaScript is used for web development. It runs in browsers and on servers.",
        )

        # Search
        python_results = await kb.search("Python programming", k=2)
        assert len(python_results) > 0

        # Hybrid search
        hybrid_results = await kb.hybrid_search("web development language", k=3)
        assert len(hybrid_results) > 0

    @pytest.mark.asyncio
    async def test_metadata_preserved(self, temp_db_path, mock_embedding_fn):
        """Test that metadata is preserved in search results."""
        kb = KB(
            db_path=temp_db_path,
            embedding_fn=mock_embedding_fn,
            chunk_size=50,
        )

        await kb.index_file(Path("/test/file.py"), "Content that will be chunked.")

        results = await kb.search("content", k=1)

        assert len(results) > 0
        assert "chunk_index" in results[0].chunk.metadata
        assert "total_chunks" in results[0].chunk.metadata
