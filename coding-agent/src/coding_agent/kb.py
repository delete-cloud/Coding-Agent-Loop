"""Knowledge Base with RAG vector search."""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import lancedb
import numpy as np
import pyarrow as pa

logger = logging.getLogger(__name__)

_METADATA_VERSION = 1
_MAX_REPO_RETRIEVAL_FETCH_K = 5000
_MAX_FAILURE_RETRIEVAL_FETCH_K = 5000
_MAX_TEST_FAILURE_SNIPPET_CHARS = 4000
_LANGUAGE_BY_SUFFIX = {
    ".bash": "shell",
    ".cfg": "config",
    ".css": "css",
    ".fish": "shell",
    ".html": "html",
    ".ini": "config",
    ".js": "javascript",
    ".json": "json",
    ".jsx": "javascript",
    ".md": "markdown",
    ".py": "python",
    ".rst": "rst",
    ".sh": "shell",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".txt": "text",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".zsh": "shell",
}

from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeRemainingColumn,
    MofNCompleteColumn,
)


@dataclass
class DocumentChunk:
    """A chunk of a document."""

    id: str
    content: str
    source: str
    metadata: dict[str, Any]


@dataclass
class KBSearchResult:
    """A search result from the knowledge base."""

    chunk: DocumentChunk
    score: float


@dataclass(frozen=True)
class RepoRetrievalResult:
    """A ranked repo-aware retrieval result."""

    rank: int
    score: float
    chunk: DocumentChunk
    chunk_id: str
    source_kind: str
    source_id: str
    repo_path: str
    language: str | None
    line_start: int | None
    line_end: int | None
    document_sha256: str | None
    chunk_sha256: str | None


@dataclass(frozen=True)
class TestFailureRetrievalResult:
    """A ranked test-failure retrieval result."""

    rank: int
    score: float
    chunk: DocumentChunk
    chunk_id: str
    source_kind: str
    source_id: str
    command_label: str
    exit_code: int
    test_node_id: str
    repo_path: str | None
    line_start: int | None
    line_end: int | None
    failure_sha256: str | None
    snippet_sha256: str | None


def _repo_relative_path(path: Path, repo_root: Path | None) -> str | None:
    path = Path(path)
    if repo_root is None:
        if path.is_absolute():
            return None
        return path.as_posix()

    root = Path(os.path.abspath(repo_root))
    candidate = path if path.is_absolute() else root / path
    candidate = Path(os.path.abspath(candidate))
    try:
        return candidate.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"{path} is outside repo_root {repo_root}") from exc


def _path_target_within_repo(path: Path, repo_root: Path) -> bool:
    root = Path(repo_root)
    candidate = path if path.is_absolute() else root / path
    if not candidate.exists() and not candidate.is_symlink():
        return True

    try:
        candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        return False
    return True


def _language_for_path(path: Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in _LANGUAGE_BY_SUFFIX:
        return _LANGUAGE_BY_SUFFIX[suffix]
    if suffix:
        return suffix.removeprefix(".")
    return "unknown"


def _source_id(
    *,
    source_kind: str,
    repo_path: str | None,
    source: str,
) -> str:
    if not source_kind.strip():
        raise ValueError("source_kind must be non-empty")
    source_key = repo_path if repo_path is not None else source
    return hashlib.sha256(f"{source_kind}:{source_key}".encode("utf-8")).hexdigest()


def _line_range_for_span(text: str, start: int, end: int) -> tuple[int, int]:
    line_start = text.count("\n", 0, start) + 1
    if end <= start:
        return line_start, line_start

    line_end = text.count("\n", 0, end)
    if text[end - 1] != "\n":
        line_end += 1
    return line_start, max(line_start, line_end)


def _optional_str(metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) else None


def _optional_int(metadata: dict[str, Any], key: str) -> int | None:
    value = metadata.get(key)
    return value if isinstance(value, int) else None


def _repo_retrieval_initial_fetch_k(k: int, max_fetch_k: int) -> int:
    if k <= 0:
        return 0
    return min(max(k * 4, k), max_fetch_k)


def _search_result_from_row(row: dict[str, Any]) -> KBSearchResult:
    import json

    return KBSearchResult(
        chunk=DocumentChunk(
            id=row["id"],
            content=row["content"],
            source=row["source"],
            metadata=json.loads(row["metadata"]),
        ),
        score=row["_distance"],
    )


def _repo_retrieval_result(
    result: KBSearchResult,
    *,
    rank: int,
) -> RepoRetrievalResult | None:
    metadata = result.chunk.metadata
    source_kind = _optional_str(metadata, "source_kind")
    source_id = _optional_str(metadata, "source_id")
    repo_path = _optional_str(metadata, "repo_path")
    if source_kind != "repo_file" or source_id is None or repo_path is None:
        return None

    return RepoRetrievalResult(
        rank=rank,
        score=result.score,
        chunk=result.chunk,
        chunk_id=result.chunk.id,
        source_kind=source_kind,
        source_id=source_id,
        repo_path=repo_path,
        language=_optional_str(metadata, "language"),
        line_start=_optional_int(metadata, "line_start"),
        line_end=_optional_int(metadata, "line_end"),
        document_sha256=_optional_str(metadata, "document_sha256"),
        chunk_sha256=_optional_str(metadata, "chunk_sha256"),
    )


def _repo_retrieval_results(
    results: list[KBSearchResult],
    *,
    k: int,
) -> list[RepoRetrievalResult]:
    repo_results: list[RepoRetrievalResult] = []
    for result in results:
        repo_result = _repo_retrieval_result(result, rank=len(repo_results) + 1)
        if repo_result is None:
            continue
        repo_results.append(repo_result)
        if len(repo_results) >= k:
            break
    return repo_results


def _test_failure_source_id(command_label: str, test_node_id: str) -> str:
    return hashlib.sha256(
        f"test_failure:{command_label}:{test_node_id}".encode("utf-8")
    ).hexdigest()


def _validate_test_failure_input(
    *,
    command_label: str,
    exit_code: int,
    test_node_id: str,
    failure_snippet: str,
    line_start: int | None,
    line_end: int | None,
) -> None:
    if not command_label.strip():
        raise ValueError("command_label must be non-empty")
    if exit_code < 0:
        raise ValueError("exit_code must be non-negative")
    if not test_node_id.strip():
        raise ValueError("test_node_id must be non-empty")
    if not failure_snippet.strip():
        raise ValueError("failure_snippet must be non-empty")
    if line_start is not None and line_start <= 0:
        raise ValueError("line_start must be positive")
    if line_end is not None and line_end <= 0:
        raise ValueError("line_end must be positive")
    if line_start is not None and line_end is not None and line_end < line_start:
        raise ValueError("line_end must be greater than or equal to line_start")


def _bounded_test_failure_snippet(failure_snippet: str) -> str:
    return failure_snippet[:_MAX_TEST_FAILURE_SNIPPET_CHARS]


def _failure_retrieval_result(
    result: KBSearchResult,
    *,
    rank: int,
) -> TestFailureRetrievalResult | None:
    metadata = result.chunk.metadata
    source_kind = _optional_str(metadata, "source_kind")
    source_id = _optional_str(metadata, "source_id")
    command_label = _optional_str(metadata, "command_label")
    exit_code = _optional_int(metadata, "exit_code")
    test_node_id = _optional_str(metadata, "test_node_id")
    if (
        source_kind != "test_failure"
        or source_id is None
        or command_label is None
        or exit_code is None
        or test_node_id is None
    ):
        return None

    return TestFailureRetrievalResult(
        rank=rank,
        score=result.score,
        chunk=result.chunk,
        chunk_id=result.chunk.id,
        source_kind=source_kind,
        source_id=source_id,
        command_label=command_label,
        exit_code=exit_code,
        test_node_id=test_node_id,
        repo_path=_optional_str(metadata, "repo_path"),
        line_start=_optional_int(metadata, "line_start"),
        line_end=_optional_int(metadata, "line_end"),
        failure_sha256=_optional_str(metadata, "failure_sha256"),
        snippet_sha256=_optional_str(metadata, "snippet_sha256"),
    )


def _failure_retrieval_results(
    results: list[KBSearchResult],
    *,
    k: int,
) -> list[TestFailureRetrievalResult]:
    failure_results: list[TestFailureRetrievalResult] = []
    for result in results:
        failure_result = _failure_retrieval_result(
            result,
            rank=len(failure_results) + 1,
        )
        if failure_result is None:
            continue
        failure_results.append(failure_result)
        if len(failure_results) >= k:
            break
    return failure_results


class KB:
    """Knowledge base with RAG vector search.

    Uses LanceDB for vector storage and supports OpenAI embeddings.
    Provides both vector search and hybrid (vector + full-text) search.
    """

    DEFAULT_CHUNK_SIZE = 1200
    DEFAULT_CHUNK_OVERLAP = 200
    DEFAULT_EMBEDDING_DIM = 1536
    DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"

    def __init__(
        self,
        db_path: Path | str,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        embedding_dim: int = DEFAULT_EMBEDDING_DIM,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        embedding_fn: Callable[[list[str]], list[list[float]]] | None = None,
        text_extensions: set[str] | None = None,
    ):
        """Initialize the knowledge base.

        Args:
            db_path: Path to the LanceDB database directory.
            embedding_model: OpenAI embedding model name.
            embedding_dim: Dimension of embedding vectors.
            chunk_size: Number of tokens per chunk.
            chunk_overlap: Number of tokens to overlap between chunks.
            embedding_fn: Optional custom embedding function for testing.
                         If not provided, uses OpenAI API.
        """
        self.db_path = Path(db_path)
        self.embedding_model = embedding_model
        self.embedding_dim = embedding_dim
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._embedding_fn = embedding_fn
        self._openai_client = None
        self._openai_sync_client = None
        self._text_extensions = text_extensions or {
            ".py",
            ".md",
            ".txt",
            ".rst",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".cfg",
            ".js",
            ".ts",
            ".jsx",
            ".tsx",
            ".html",
            ".css",
            ".sh",
            ".bash",
            ".zsh",
            ".fish",
        }

        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Connect to LanceDB
        self._db = lancedb.connect(str(self.db_path))
        self._table: lancedb.table.Table | None = None

    def _table_names(self) -> set[str]:
        listed = self._db.list_tables()
        if isinstance(listed, list):
            return {str(name) for name in listed}

        tables = getattr(listed, "tables", None)
        if isinstance(tables, list):
            return {str(name) for name in tables}

        raise TypeError(f"unsupported list_tables() result: {type(listed)!r}")

    def _get_openai_client(self):
        """Get or create OpenAI client."""
        if self._openai_client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise ImportError(
                    "OpenAI package is required for embeddings. "
                    "Install it with: pip install openai"
                )

            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise ValueError(
                    "OPENAI_API_KEY environment variable is required "
                    "when not using a custom embedding function"
                )
            self._openai_client = AsyncOpenAI(api_key=api_key)
        return self._openai_client

    def _get_openai_sync_client(self):
        if self._openai_sync_client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise ImportError(
                    "OpenAI package is required for embeddings. "
                    "Install it with: pip install openai"
                )

            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise ValueError(
                    "OPENAI_API_KEY environment variable is required "
                    "when not using a custom embedding function"
                )
            self._openai_sync_client = OpenAI(api_key=api_key)
        return self._openai_sync_client

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts using OpenAI or custom embedding function.

        Args:
            texts: List of texts to embed.

        Returns:
            List of embedding vectors.
        """
        if self._embedding_fn is not None:
            return self._embedding_fn(texts)

        client = self._get_openai_client()
        response = await client.embeddings.create(
            model=self.embedding_model,
            input=texts,
        )
        return [item.embedding for item in response.data]

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        if self._embedding_fn is not None:
            return self._embedding_fn(texts)

        client = self._get_openai_sync_client()
        response = client.embeddings.create(
            model=self.embedding_model,
            input=texts,
        )
        return [item.embedding for item in response.data]

    def _chunk_text(self, text: str) -> list[str]:
        """Split text into overlapping chunks by token count.

        Uses a simple approximation: ~4 characters per token.

        Args:
            text: The text to chunk.

        Returns:
            List of text chunks.
        """
        return [chunk for chunk, _start, _end in self._chunk_text_with_spans(text)]

    def _chunk_text_with_spans(self, text: str) -> list[tuple[str, int, int]]:
        chars_per_token = 4
        chunk_chars = self.chunk_size * chars_per_token
        overlap_chars = self.chunk_overlap * chars_per_token

        if len(text) <= chunk_chars:
            return [(text, 0, len(text))]

        chunks: list[tuple[str, int, int]] = []
        start = 0

        while start < len(text):
            end = start + chunk_chars
            chunk = text[start:end]
            chunks.append((chunk, start, min(end, len(text))))
            start += chunk_chars - overlap_chars

            # Avoid infinite loop for very small texts
            if overlap_chars >= chunk_chars:
                break

        return chunks

    def _get_table(self) -> lancedb.table.Table:
        """Get or create the LanceDB table.

        Returns:
            The chunks table.
        """
        if self._table is not None:
            return self._table

        table_name = "chunks"

        if table_name in self._table_names():
            self._table = self._db.open_table(table_name)
        else:
            # Create table with schema
            schema = pa.schema(
                [
                    ("id", pa.string()),
                    ("content", pa.string()),
                    ("source", pa.string()),
                    ("metadata", pa.string()),  # JSON string
                    ("vector", pa.list_(pa.float64(), self.embedding_dim)),
                ]
            )
            self._table = self._db.create_table(table_name, schema=schema)

        return self._table

    def has_table(self, table_name: str = "chunks") -> bool:
        return table_name in self._table_names()

    async def index_file(
        self,
        path: Path,
        content: str,
        *,
        repo_root: Path | None = None,
        source_kind: str = "repo_file",
    ) -> None:
        """Index a single file into the knowledge base.

        Args:
            path: The file path (used as source identifier).
            content: The file content to index.
        """
        path = Path(path)
        source = str(path)
        if repo_root is not None and not _path_target_within_repo(path, repo_root):
            raise ValueError(f"{path} target is outside repo_root {repo_root}")
        repo_path = _repo_relative_path(path, repo_root)
        document_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        source_id = _source_id(
            source_kind=source_kind,
            repo_path=repo_path,
            source=source,
        )

        table = self._get_table()

        # Split content into chunks
        chunk_spans = self._chunk_text_with_spans(content)
        chunks = [chunk for chunk, _start, _end in chunk_spans]

        if not chunks or all(not c.strip() for c in chunks):
            return

        # Generate embeddings
        embeddings = await self._embed(chunks)

        # Prepare data for insertion
        import json

        data = []
        for i, ((chunk_content, start, end), embedding) in enumerate(
            zip(chunk_spans, embeddings, strict=True)
        ):
            # Generate deterministic ID based on content hash
            content_hash = hashlib.sha256(
                f"{source}:{i}:{chunk_content}".encode()
            ).hexdigest()
            chunk_id = f"{uuid.uuid4().hex[:8]}_{content_hash[:16]}"
            line_start, line_end = _line_range_for_span(content, start, end)

            metadata = {
                "metadata_version": _METADATA_VERSION,
                "source_kind": source_kind,
                "source_id": source_id,
                "repo_path": repo_path,
                "language": _language_for_path(path),
                "document_sha256": document_sha256,
                "chunk_sha256": hashlib.sha256(
                    chunk_content.encode("utf-8")
                ).hexdigest(),
                "chunk_index": i,
                "total_chunks": len(chunks),
                "line_start": line_start,
                "line_end": line_end,
            }

            data.append(
                {
                    "id": chunk_id,
                    "content": chunk_content,
                    "source": source,
                    "metadata": json.dumps(metadata),
                    "vector": embedding,
                }
            )

        # Insert into LanceDB
        table.add(data)

    async def index_test_failure(
        self,
        *,
        command_label: str,
        exit_code: int,
        test_node_id: str,
        failure_snippet: str,
        repo_path: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
    ) -> None:
        _validate_test_failure_input(
            command_label=command_label,
            exit_code=exit_code,
            test_node_id=test_node_id,
            failure_snippet=failure_snippet,
            line_start=line_start,
            line_end=line_end,
        )

        snippet = _bounded_test_failure_snippet(failure_snippet)
        embeddings = await self._embed([snippet])
        if len(embeddings) != 1:
            raise ValueError("embedding function must return exactly one embedding")

        failure_sha256 = hashlib.sha256(failure_snippet.encode("utf-8")).hexdigest()
        snippet_sha256 = hashlib.sha256(snippet.encode("utf-8")).hexdigest()
        source_id = _test_failure_source_id(command_label, test_node_id)
        chunk_id = f"failure_{source_id[:12]}_{snippet_sha256[:12]}"
        metadata = {
            "metadata_version": _METADATA_VERSION,
            "source_kind": "test_failure",
            "source_id": source_id,
            "command_label": command_label,
            "exit_code": exit_code,
            "test_node_id": test_node_id,
            "repo_path": repo_path,
            "line_start": line_start,
            "line_end": line_end,
            "failure_sha256": failure_sha256,
            "snippet_sha256": snippet_sha256,
        }

        import json

        row = {
            "id": chunk_id,
            "content": snippet,
            "source": test_node_id,
            "metadata": json.dumps(metadata),
            "vector": embeddings[0],
        }
        table = self._get_table()
        (
            table.merge_insert("id")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute([row])
        )

    async def index_directory(
        self,
        root: Path,
        pattern: str = "**/*",
        show_progress: bool = True,
    ) -> None:
        """Index all text files in a directory.

        Args:
            root: Root directory to scan for files.
            pattern: File glob pattern (default: all files).
            show_progress: Whether to show progress bar (default: True).
        """
        root = Path(root)
        # Collect all files to index
        files = [
            path
            for path in root.rglob(pattern)
            if path.is_file() and path.suffix in self._text_extensions
        ]

        if not files:
            return

        # Check if we should show progress (not in non-TTY environment)
        show_progress = show_progress and sys.stdout.isatty()

        if not show_progress:
            # Original implementation without progress
            for path in files:
                try:
                    if not _path_target_within_repo(path, root):
                        continue
                    content = path.read_text(encoding="utf-8")
                    await self.index_file(path, content, repo_root=root)
                except (IOError, UnicodeDecodeError, ValueError):
                    continue
            return

        # With progress bar
        console = Console(stderr=True)  # Write progress to stderr
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(complete_style="green", finished_style="green"),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            transient=True,  # Hide after completion
            console=console,
        )

        errors = []
        with progress:
            task = progress.add_task(
                f"Indexing {root.name}...",
                total=len(files),
            )

            for path in files:
                # Update description with current file
                progress.update(task, description=f"Indexing [cyan]{path.name}")

                try:
                    if not _path_target_within_repo(path, root):
                        continue
                    content = path.read_text(encoding="utf-8")
                    await self.index_file(path, content, repo_root=root)
                except (IOError, UnicodeDecodeError, ValueError) as e:
                    errors.append((path, e))
                finally:
                    progress.advance(task)

        # Summary
        if errors:
            console.print(
                f"[yellow]⚠[/yellow] Indexed {len(files) - len(errors)}/{len(files)} files ({len(errors)} errors)"
            )
        else:
            console.print(f"[green]✓[/green] Indexed {len(files)} files")

    async def search(self, query: str, k: int = 5) -> list[KBSearchResult]:
        """Search for relevant chunks using vector search.

        Args:
            query: The search query.
            k: Number of results to return.

        Returns:
            List of search results sorted by relevance.
        """
        if not query.strip():
            return []

        table = self._get_table()

        # Get query embedding
        embeddings = await self._embed([query])
        query_vector = embeddings[0]

        # Perform vector search
        import json

        results = table.search(query_vector).limit(k).to_list()

        return [
            KBSearchResult(
                chunk=DocumentChunk(
                    id=r["id"],
                    content=r["content"],
                    source=r["source"],
                    metadata=json.loads(r["metadata"]),
                ),
                score=r["_distance"],
            )
            for r in results
        ]

    async def search_repo(
        self,
        query: str,
        k: int = 5,
    ) -> list[RepoRetrievalResult]:
        if not query.strip() or k <= 0:
            return []
        if k > _MAX_REPO_RETRIEVAL_FETCH_K:
            raise ValueError(
                f"k must be less than or equal to {_MAX_REPO_RETRIEVAL_FETCH_K}"
            )
        if not self.has_table():
            return []

        table = self._get_table()
        embeddings = await self._embed([query])
        query_vector = embeddings[0]
        fetch_k = _repo_retrieval_initial_fetch_k(k, _MAX_REPO_RETRIEVAL_FETCH_K)

        while True:
            rows = table.search(query_vector).limit(fetch_k).to_list()
            fetched = [_search_result_from_row(row) for row in rows]
            repo_results = _repo_retrieval_results(fetched, k=k)
            if (
                len(repo_results) >= k
                or len(rows) < fetch_k
                or fetch_k >= _MAX_REPO_RETRIEVAL_FETCH_K
            ):
                return repo_results
            fetch_k = min(fetch_k * 2, _MAX_REPO_RETRIEVAL_FETCH_K)

    async def search_test_failures(
        self,
        query: str,
        k: int = 5,
    ) -> list[TestFailureRetrievalResult]:
        if not query.strip() or k <= 0:
            return []
        if k > _MAX_FAILURE_RETRIEVAL_FETCH_K:
            raise ValueError(
                f"k must be less than or equal to {_MAX_FAILURE_RETRIEVAL_FETCH_K}"
            )
        if not self.has_table():
            return []

        table = self._get_table()
        embeddings = await self._embed([query])
        query_vector = embeddings[0]
        fetch_k = _repo_retrieval_initial_fetch_k(k, _MAX_FAILURE_RETRIEVAL_FETCH_K)

        while True:
            rows = table.search(query_vector).limit(fetch_k).to_list()
            fetched = [_search_result_from_row(row) for row in rows]
            failure_results = _failure_retrieval_results(fetched, k=k)
            if (
                len(failure_results) >= k
                or len(rows) < fetch_k
                or fetch_k >= _MAX_FAILURE_RETRIEVAL_FETCH_K
            ):
                return failure_results
            fetch_k = min(fetch_k * 2, _MAX_FAILURE_RETRIEVAL_FETCH_K)

    def search_sync(self, query: str, k: int = 5) -> list[KBSearchResult]:
        if not query.strip():
            return []

        if not self.has_table():
            return []

        table = self._get_table()

        embeddings = self._embed_sync([query])
        query_vector = embeddings[0]

        import json

        results = table.search(query_vector).limit(k).to_list()

        return [
            KBSearchResult(
                chunk=DocumentChunk(
                    id=r["id"],
                    content=r["content"],
                    source=r["source"],
                    metadata=json.loads(r["metadata"]),
                ),
                score=r["_distance"],
            )
            for r in results
        ]

    async def hybrid_search(self, query: str, k: int = 5) -> list[KBSearchResult]:
        """Search using hybrid approach: full-text + vector search.

        Performs both full-text search and vector search, then merges
        and deduplicates results.

        Args:
            query: The search query.
            k: Number of results to return.

        Returns:
            List of merged search results sorted by relevance.
        """
        if not query.strip():
            return []

        table = self._get_table()

        # Get query embedding for vector search
        embeddings = await self._embed([query])
        query_vector = embeddings[0]

        # Perform both searches
        import json

        # Vector search
        vector_results = table.search(query_vector).limit(k).to_list()

        # Full-text search (using LanceDB's full-text search)
        fts_results: list[dict[str, Any]] = []
        try:
            fts_results = table.search(query, query_type="fts").limit(k).to_list()
        except (RuntimeError, NotImplementedError):
            # FTS might not be available or index not built, fall back to vector only
            logger.debug("Full-text search not available, using vector search only")

        # Merge and deduplicate results
        seen_ids = set()
        merged = []

        # Process vector results first (usually higher quality)
        for r in vector_results:
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                raw_distance = float(r["_distance"])
                merged.append(
                    KBSearchResult(
                        chunk=DocumentChunk(
                            id=r["id"],
                            content=r["content"],
                            source=r["source"],
                            metadata=json.loads(r["metadata"]),
                        ),
                        score=raw_distance * 0.9,
                    )
                )

        # Add FTS results
        for r in fts_results:
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                raw_score = float(r.get("_score", 0.0))
                merged.append(
                    KBSearchResult(
                        chunk=DocumentChunk(
                            id=r["id"],
                            content=r["content"],
                            source=r["source"],
                            metadata=json.loads(r["metadata"]),
                        ),
                        score=-raw_score,
                    )
                )

        merged.sort(key=lambda x: x.score)

        return merged[:k]
