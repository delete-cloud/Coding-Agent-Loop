# Citation Policy

For a coding assistant with RAG:

- Every KB-dependent answer should include source paths.
- Citations should point to concrete files, not vague descriptions.
- If evidence is missing, the agent should explicitly say insufficient context.
- Citation quality matters more than citation quantity.

A minimal acceptable citation format is a repository-relative path such as `eval/ab/kb/rag_pipeline.md`.
