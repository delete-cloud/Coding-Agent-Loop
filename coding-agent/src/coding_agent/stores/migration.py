from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from agentkit.storage.checkpoint_fs import FSCheckpointStore
from agentkit.storage.sqlite import SQLiteCheckpointStore, SQLiteTapeStore
from coding_agent.stores.local import local_sqlite_path


@dataclass(frozen=True)
class StoreMigrationReport:
    scanned: int = 0
    migrated: int = 0
    skipped: int = 0


@dataclass(frozen=True)
class LegacySQLiteMigrationReport:
    tapes: StoreMigrationReport
    checkpoints: StoreMigrationReport


async def migrate_legacy_storage_to_sqlite(
    data_dir: Path,
    *,
    tapes_dir: Path | None = None,
    checkpoints_dir: Path | None = None,
    tape_sqlite_path: Path | None = None,
    checkpoint_sqlite_path: Path | None = None,
    replace_tapes: bool = False,
    dry_run: bool = False,
) -> LegacySQLiteMigrationReport:
    resolved_data_dir = Path(data_dir)
    return LegacySQLiteMigrationReport(
        tapes=await migrate_jsonl_tapes_to_sqlite(
            tapes_dir or resolved_data_dir / "tapes",
            tape_sqlite_path or local_sqlite_path(resolved_data_dir),
            replace=replace_tapes,
            dry_run=dry_run,
        ),
        checkpoints=await migrate_fs_checkpoints_to_sqlite(
            checkpoints_dir or resolved_data_dir / "checkpoints",
            checkpoint_sqlite_path or local_sqlite_path(resolved_data_dir),
            dry_run=dry_run,
        ),
    )


async def migrate_jsonl_tapes_to_sqlite(
    source_dir: Path,
    sqlite_path: Path,
    *,
    replace: bool = False,
    dry_run: bool = False,
) -> StoreMigrationReport:
    source_dir = Path(source_dir)
    sqlite_path = Path(sqlite_path)
    if not source_dir.exists():
        return StoreMigrationReport()
    if not source_dir.is_dir():
        raise NotADirectoryError(str(source_dir))

    store = (
        None if dry_run and not sqlite_path.exists() else SQLiteTapeStore(sqlite_path)
    )
    scanned = 0
    migrated = 0
    skipped = 0
    for path in sorted(source_dir.glob("*.jsonl")):
        scanned += 1
        tape_id = path.stem
        entries = _read_jsonl_objects(path)
        existing = [] if store is None else await store.load(tape_id)
        if not entries and not existing:
            skipped += 1
            continue
        if existing:
            if existing == entries:
                skipped += 1
                continue
            if not replace:
                raise ValueError(
                    f"tape {tape_id!r} already exists with different entries"
                )
            if not dry_run:
                if store is None:
                    store = SQLiteTapeStore(sqlite_path)
                await store.truncate(tape_id, 0)
        if not dry_run:
            if store is None:
                store = SQLiteTapeStore(sqlite_path)
            await store.save(tape_id, entries)
        migrated += 1
    return StoreMigrationReport(scanned=scanned, migrated=migrated, skipped=skipped)


async def migrate_fs_checkpoints_to_sqlite(
    source_dir: Path,
    sqlite_path: Path,
    *,
    dry_run: bool = False,
) -> StoreMigrationReport:
    source_dir = Path(source_dir)
    sqlite_path = Path(sqlite_path)
    if not source_dir.exists():
        return StoreMigrationReport()
    if not source_dir.is_dir():
        raise NotADirectoryError(str(source_dir))

    source_store = FSCheckpointStore(source_dir)
    target_store = (
        None
        if dry_run and not sqlite_path.exists()
        else SQLiteCheckpointStore(sqlite_path)
    )
    scanned = 0
    migrated = 0
    skipped = 0
    for meta_path in sorted(source_dir.glob("*.meta.json")):
        checkpoint_id = meta_path.name.removesuffix(".meta.json")
        scanned += 1
        snapshot = await source_store.load(checkpoint_id)
        if snapshot is None:
            raise RuntimeError(
                f"checkpoint metadata {meta_path} did not load checkpoint snapshot"
            )
        existing = (
            None if target_store is None else await target_store.load(checkpoint_id)
        )
        if existing == snapshot:
            skipped += 1
            continue
        if not dry_run:
            if target_store is None:
                target_store = SQLiteCheckpointStore(sqlite_path)
            await target_store.save(snapshot)
        migrated += 1
    return StoreMigrationReport(scanned=scanned, migrated=migrated, skipped=skipped)


def _read_jsonl_objects(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            loaded = json.loads(stripped)
            if not isinstance(loaded, dict):
                raise TypeError(
                    f"{path}:{line_number} must contain a JSON object tape entry"
                )
            entries.append(cast(dict[str, Any], loaded))
    return entries


__all__ = [
    "LegacySQLiteMigrationReport",
    "StoreMigrationReport",
    "migrate_fs_checkpoints_to_sqlite",
    "migrate_jsonl_tapes_to_sqlite",
    "migrate_legacy_storage_to_sqlite",
]
