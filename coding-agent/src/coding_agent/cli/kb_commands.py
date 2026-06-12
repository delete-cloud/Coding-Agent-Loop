from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import click

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def _load_kb_cli_settings(
    config_path: Path, db_path: str | None
) -> tuple[Path, dict[str, Any]]:
    from agentkit.config.loader import load_config

    kb_cfg: dict[str, Any] = {}
    if config_path.exists():
        agent_cfg = load_config(config_path)
        raw_kb_cfg = agent_cfg.extra.get("kb", {})
        if isinstance(raw_kb_cfg, dict):
            kb_cfg = raw_kb_cfg

    resolved_db = (
        Path(db_path)
        if db_path is not None
        else Path(os.environ.get("AGENT_DATA_DIR", "./data"))
        / str(kb_cfg.get("db_path", "kb"))
    )
    return resolved_db, kb_cfg


def _kb_corpus(kb_cfg: dict[str, Any], override: str | None) -> str:
    value = override if override is not None else kb_cfg.get("corpus", "default")
    if not isinstance(value, str):
        raise TypeError("[kb].corpus must be a string")
    return value


def _kb_search_corpora(
    kb_cfg: dict[str, Any],
    overrides: tuple[str, ...],
) -> tuple[str, ...] | None:
    if overrides:
        return overrides
    configured = kb_cfg.get("search_corpora")
    if configured is None:
        return None
    if not isinstance(configured, list):
        raise TypeError("[kb].search_corpora must be a list")
    if not all(isinstance(corpus, str) for corpus in configured):
        raise TypeError("[kb].search_corpora must contain only strings")
    return tuple(configured)


@click.group()
def kb():
    pass


@kb.command("index")
@click.argument("path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--db-path",
    default=None,
    help="LanceDB database path (default: from agent.toml [kb].db_path)",
)
@click.option(
    "--prune",
    is_flag=True,
    help="Delete indexed chunks for files under PATH that no longer exist.",
)
@click.option(
    "--corpus",
    default=None,
    help="Corpus name for indexed chunks (default: from agent.toml [kb].corpus).",
)
def kb_index(path: Path, db_path: str | None, prune: bool, corpus: str | None):
    import asyncio

    from coding_agent.kb import KB

    config_path = _PACKAGE_ROOT / "agent.toml"
    resolved_db, kb_cfg = _load_kb_cli_settings(config_path, db_path)

    raw_extensions = kb_cfg.get(
        "index_extensions",
        [".md", ".txt", ".rst", ".yaml", ".yml", ".toml"],
    )
    if not isinstance(raw_extensions, list):
        raise TypeError("[kb].index_extensions must be a list")

    kb_instance = KB(
        db_path=resolved_db,
        embedding_model=str(kb_cfg.get("embedding_model", "text-embedding-3-small")),
        embedding_base_url=kb_cfg.get("embedding_base_url"),
        embedding_dim=int(kb_cfg.get("embedding_dim", 1536)),
        chunk_size=int(kb_cfg.get("chunk_size", 1200)),
        chunk_overlap=int(kb_cfg.get("chunk_overlap", 200)),
        text_extensions={str(ext) for ext in raw_extensions},
        corpus=_kb_corpus(kb_cfg, corpus),
    )

    asyncio.run(kb_instance.index_directory(path, show_progress=False, prune=prune))
    click.echo("Done.")


@kb.command("search")
@click.argument("query")
@click.option("--k", default=5, type=int, help="Number of results to return")
@click.option(
    "--db-path",
    default=None,
    help="LanceDB database path (default: from agent.toml [kb].db_path)",
)
@click.option(
    "--corpus",
    "corpora",
    multiple=True,
    help="Limit search to a corpus. Repeat to search multiple corpora.",
)
def kb_search(query: str, k: int, db_path: str | None, corpora: tuple[str, ...]):
    from coding_agent.kb import KB

    config_path = _PACKAGE_ROOT / "agent.toml"
    resolved_db, kb_cfg = _load_kb_cli_settings(config_path, db_path)

    kb_instance = KB(
        db_path=resolved_db,
        embedding_model=str(kb_cfg.get("embedding_model", "text-embedding-3-small")),
        embedding_base_url=kb_cfg.get("embedding_base_url"),
        embedding_dim=int(kb_cfg.get("embedding_dim", 1536)),
        chunk_size=int(kb_cfg.get("chunk_size", 1200)),
        chunk_overlap=int(kb_cfg.get("chunk_overlap", 200)),
    )

    if not kb_instance.has_table():
        click.echo("No index found. Run 'kb index <path>' first.")
        return

    results = kb_instance.search_sync(
        query,
        k=k,
        corpora=_kb_search_corpora(kb_cfg, corpora),
    )
    if not results:
        click.echo("No results found.")
        return

    for index, result in enumerate(results, start=1):
        click.echo(f"\n--- Result {index} (score: {result.score:.4f}) ---")
        click.echo(f"Source: {result.chunk.source}")
        content = result.chunk.content
        if len(content) > 200:
            content = content[:200] + "..."
        click.echo(content)


@kb.command("corpora")
@click.option(
    "--db-path",
    default=None,
    help="LanceDB database path (default: from agent.toml [kb].db_path)",
)
def kb_corpora(db_path: str | None):
    from coding_agent.kb import KB

    config_path = _PACKAGE_ROOT / "agent.toml"
    resolved_db, kb_cfg = _load_kb_cli_settings(config_path, db_path)

    kb_instance = KB(
        db_path=resolved_db,
        embedding_model=str(kb_cfg.get("embedding_model", "text-embedding-3-small")),
        embedding_base_url=kb_cfg.get("embedding_base_url"),
        embedding_dim=int(kb_cfg.get("embedding_dim", 1536)),
        chunk_size=int(kb_cfg.get("chunk_size", 1200)),
        chunk_overlap=int(kb_cfg.get("chunk_overlap", 200)),
    )

    counts = kb_instance.corpus_counts()
    if not counts:
        click.echo("No index found. Run 'kb index <path>' first.")
        return
    for corpus, count in sorted(counts.items()):
        click.echo(f"{corpus}\t{count}")
