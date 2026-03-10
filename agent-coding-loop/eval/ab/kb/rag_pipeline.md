# RAG Pipeline

A practical RAG pipeline for interview explanation:

1. Loader reads repository files and docs.
2. Chunking splits long text into retrieval units.
3. Embedding maps chunks into vectors.
4. Hybrid retrieval combines vector similarity and keyword matching.
5. Rerank sorts top-k candidates to improve precision.
6. Generator answers with citations.

When presenting in interview, emphasize why each stage is needed rather than only listing components.
