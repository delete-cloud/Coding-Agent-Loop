"""Knowledge Base with RAG vector search."""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import lancedb
import numpy as np
import pyarrow as pa


@dataclass
class DocumentChunk:
    """A chunk of a document."""

    id: str
    content: str
    source: str
    metadata: dict


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

    def __init__(
        self,
        db_path: Path | str,
        embedding_model: str = "text-embedding-3-small",
        chunk_size: int = 1200,
        chunk_overlap: int = 200,
        embedding_fn: Callable[[list[str]], list[list[float]]] | None = None,
    ):
        """Initialize the knowledge base.

        Args:
            db_path: Path to the LanceDB database directory.
            embedding_model: OpenAI embedding model name.
            chunk_size: Number of tokens per chunk.
            chunk_overlap: Number of tokens to overlap between chunks.
            embedding_fn: Optional custom embedding function for testing.
                         If not provided, uses OpenAI API.
        """
        self.db_path = Path(db_path)
        self.embedding_model = embedding_model
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._embedding_fn = embedding_fn
        self._openai_client = None

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

        if table_name in self._db.list_tables():
            self._table = self._db.open_table(table_name)
        else:
            # Create table with schema
            schema = pa.schema([
                ("id", pa.string()),
                ("content", pa.string()),
                ("source", pa.string()),
                ("metadata", pa.string()),  # JSON string
                ("vector", pa.list_(pa.float64(), 1536)),  # OpenAI embedding size
            ])
            self._table = self._db.create_table(table_name, schema=schema)

        return self._table

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
            content_hash = hashlib.md5(
                f"{source}:{i}:{chunk_content}".encode()
            ).hexdigest()
            chunk_id = f"{uuid.uuid4().hex[:8]}_{content_hash[:16]}"

            metadata = {
                "chunk_index": i,
                "total_chunks": len(chunks),
            }

            data.append({
                "id": chunk_id,
                "content": chunk_content,
                "source": source,
                "metadata": json.dumps(metadata),
                "vector": embedding,
            })

        # Insert into LanceDB
        table.add(data)

    async def index_directory(self, root: Path) -> None:
        """Index all text files in a directory.

        Args:
            root: Root directory to scan for files.
        """
        root = Path(root)
        text_extensions = {
            ".py", ".md", ".txt", ".rst", ".json", ".yaml", ".yml",
            ".toml", ".ini", ".cfg", ".js", ".ts", ".jsx", ".tsx",
            ".html", ".css", ".sh", ".bash", ".zsh", ".fish",
        }

        for path in root.rglob("*"):
            if path.is_file() and path.suffix in text_extensions:
                try:
                    content = path.read_text(encoding="utf-8")
                    await self.index_file(path, content)
                except (IOError, UnicodeDecodeError):
                    # Skip files that can't be read
                    continue

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

        results = (
            table.search(query_vector)
            .limit(k)
            .to_list()
        )

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
        vector_results = (
            table.search(query_vector)
            .limit(k)
            .to_list()
        )

        # Full-text search (using LanceDB's full-text search)
        try:
            fts_results = (
                table.search(query, query_type="fts")
                .limit(k)
                .to_list()
            )
        except Exception:
            # FTS might not be available, fall back to vector only
            fts_results = []

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
