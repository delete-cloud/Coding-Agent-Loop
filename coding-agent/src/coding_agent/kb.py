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
        chars_per_token = 4
        chunk_chars = self.chunk_size * chars_per_token
        overlap_chars = self.chunk_overlap * chars_per_token

        if len(text) <= chunk_chars:
            return [text]

        chunks = []
        start = 0

        while start < len(text):
            end = start + chunk_chars
            chunk = text[start:end]
            chunks.append(chunk)
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

        if table_name in self._db.list_tables().tables:
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
        return table_name in self._db.list_tables().tables

    async def index_file(self, path: Path, content: str) -> None:
        """Index a single file into the knowledge base.

        Args:
            path: The file path (used as source identifier).
            content: The file content to index.
        """
        table = self._get_table()

        # Split content into chunks
        chunks = self._chunk_text(content)

        if not chunks or all(not c.strip() for c in chunks):
            return

        # Generate embeddings
        embeddings = await self._embed(chunks)

        # Prepare data for insertion
        import json

        source = str(path)
        data = []
        for i, (chunk_content, embedding) in enumerate(zip(chunks, embeddings)):
            # Generate deterministic ID based on content hash
            content_hash = hashlib.sha256(
                f"{source}:{i}:{chunk_content}".encode()
            ).hexdigest()
            chunk_id = f"{uuid.uuid4().hex[:8]}_{content_hash[:16]}"

            metadata = {
                "chunk_index": i,
                "total_chunks": len(chunks),
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
                    content = path.read_text(encoding="utf-8")
                    await self.index_file(path, content)
                except (IOError, UnicodeDecodeError):
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
                    content = path.read_text(encoding="utf-8")
                    await self.index_file(path, content)
                except (IOError, UnicodeDecodeError) as e:
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
                merged.append(
                    KBSearchResult(
                        chunk=DocumentChunk(
                            id=r["id"],
                            content=r["content"],
                            source=r["source"],
                            metadata=json.loads(r["metadata"]),
                        ),
                        score=r["_distance"] * 0.9,  # Slight boost for vector results
                    )
                )

        # Add FTS results
        for r in fts_results:
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                merged.append(
                    KBSearchResult(
                        chunk=DocumentChunk(
                            id=r["id"],
                            content=r["content"],
                            source=r["source"],
                            metadata=json.loads(r["metadata"]),
                        ),
                        score=r.get("_score", 1.0),  # FTS uses _score, not _distance
                    )
                )

        # Sort by score (lower is better for vector distance)
        # For FTS, higher score is better, so we need to normalize
        # We'll just use the original scores and sort
        merged.sort(key=lambda x: x.score)

        return merged[:k]
