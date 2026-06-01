from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

from agentkit.checkpoint.models import CheckpointMeta, CheckpointSnapshot
from agentkit.storage.protocols import TapeInfo, TapeSearchResult


class SQLiteTapeStore:
    _CREATE_SCHEMA_SQL: Final[str] = """
    CREATE TABLE IF NOT EXISTS tape_entries (
        tape_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        entry_json TEXT NOT NULL,
        kind TEXT,
        run_id TEXT,
        tool_call_id TEXT,
        anchor_type TEXT,
        created_at TEXT NOT NULL,
        PRIMARY KEY (tape_id, seq)
    );

    CREATE INDEX IF NOT EXISTS tape_entries_tape_id_seq_idx
        ON tape_entries (tape_id, seq);
    CREATE INDEX IF NOT EXISTS tape_entries_kind_idx
        ON tape_entries (kind, tape_id, seq);
    CREATE INDEX IF NOT EXISTS tape_entries_run_id_idx
        ON tape_entries (run_id, tape_id, seq);
    CREATE INDEX IF NOT EXISTS tape_entries_tool_call_id_idx
        ON tape_entries (tool_call_id, tape_id, seq);
    CREATE INDEX IF NOT EXISTS tape_entries_anchor_type_idx
        ON tape_entries (anchor_type, tape_id, seq);
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(self._CREATE_SCHEMA_SQL)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    async def save(self, tape_id: str, entries: list[dict[str, Any]]) -> None:
        _require_non_empty("tape_id", tape_id)
        if not entries:
            return
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), -1) AS max_seq FROM tape_entries WHERE tape_id = ?",
                (tape_id,),
            ).fetchone()
            max_seq = _row_required_int(row, "max_seq", context="tape max seq")
            values = []
            now = datetime.now(UTC).isoformat()
            for offset, entry in enumerate(entries, start=1):
                _require_json_object("tape entry", entry)
                values.append(
                    (
                        tape_id,
                        max_seq + offset,
                        json.dumps(entry, sort_keys=True),
                        _optional_entry_str(entry, "kind"),
                        _entry_nested_str(entry, "run_id"),
                        _entry_nested_str(entry, "tool_call_id"),
                        _entry_anchor_type(entry),
                        now,
                    )
                )
            connection.executemany(
                """
                INSERT INTO tape_entries (
                    tape_id, seq, entry_json, kind, run_id, tool_call_id,
                    anchor_type, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )

    async def load(self, tape_id: str) -> list[dict[str, Any]]:
        _require_non_empty("tape_id", tape_id)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT entry_json
                FROM tape_entries
                WHERE tape_id = ?
                ORDER BY seq
                """,
                (tape_id,),
            ).fetchall()
        return [
            _json_object_from_text(row["entry_json"], context="tape entry")
            for row in rows
        ]

    async def list_ids(self) -> list[str]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT tape_id FROM tape_entries ORDER BY tape_id"
            ).fetchall()
        return [_row_required_str(row, "tape_id", context="tape id") for row in rows]

    async def truncate(self, tape_id: str, keep: int) -> None:
        _require_non_empty("tape_id", tape_id)
        if keep < 0:
            raise ValueError("keep must be >= 0")
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM tape_entries WHERE tape_id = ? AND seq >= ?",
                (tape_id, keep),
            )

    async def info(self, tape_id: str) -> TapeInfo | None:
        _require_non_empty("tape_id", tape_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    tape_id,
                    COUNT(*) AS entry_count,
                    MIN(seq) AS first_seq,
                    MAX(seq) AS last_seq
                FROM tape_entries
                WHERE tape_id = ?
                GROUP BY tape_id
                """,
                (tape_id,),
            ).fetchone()
        if row is None:
            return None
        return TapeInfo(
            tape_id=_row_required_str(row, "tape_id", context="tape info"),
            entry_count=_row_required_int(row, "entry_count", context="tape info"),
            first_seq=_row_required_int(row, "first_seq", context="tape info"),
            last_seq=_row_required_int(row, "last_seq", context="tape info"),
        )

    async def search(
        self,
        *,
        tape_id: str | None = None,
        kind: str | None = None,
        run_id: str | None = None,
        tool_call_id: str | None = None,
        anchor_type: str | None = None,
        limit: int = 100,
    ) -> list[TapeSearchResult]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        filters: list[str] = []
        values: list[object] = []
        for column, value in (
            ("tape_id", tape_id),
            ("kind", kind),
            ("run_id", run_id),
            ("tool_call_id", tool_call_id),
            ("anchor_type", anchor_type),
        ):
            if value is not None:
                filters.append(f"{column} = ?")
                values.append(value)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        values.append(limit)
        query = f"""
            SELECT tape_id, seq, entry_json
            FROM tape_entries
            {where}
            ORDER BY tape_id, seq
            LIMIT ?
        """
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [
            TapeSearchResult(
                tape_id=_row_required_str(row, "tape_id", context="tape search"),
                seq=_row_required_int(row, "seq", context="tape search"),
                entry=_json_object_from_text(
                    row["entry_json"],
                    context="tape search entry",
                ),
            )
            for row in rows
        ]

    def append_memory_record(self, tape_id: str, record: dict[str, Any]) -> None:
        _require_non_empty("tape_id", tape_id)
        _require_json_object("memory record", record)
        entry = {
            "id": _new_entry_id(),
            "kind": "memory_record",
            "payload": record,
            "timestamp": datetime.now(UTC).timestamp(),
        }
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), -1) AS max_seq FROM tape_entries WHERE tape_id = ?",
                (tape_id,),
            ).fetchone()
            seq = _row_required_int(row, "max_seq", context="tape max seq") + 1
            connection.execute(
                """
                INSERT INTO tape_entries (
                    tape_id, seq, entry_json, kind, run_id, tool_call_id,
                    anchor_type, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tape_id,
                    seq,
                    json.dumps(entry, sort_keys=True),
                    "memory_record",
                    None,
                    None,
                    None,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def load_memory_records(self, tape_id: str) -> list[dict[str, Any]]:
        _require_non_empty("tape_id", tape_id)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT entry_json
                FROM tape_entries
                WHERE tape_id = ? AND kind = 'memory_record'
                ORDER BY seq
                """,
                (tape_id,),
            ).fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            entry = _json_object_from_text(row["entry_json"], context="memory entry")
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                raise TypeError("memory entry payload must be a JSON object")
            records.append(cast(dict[str, Any], payload))
        return records

    def replace_memory_records(
        self,
        tape_id: str,
        records: list[dict[str, Any]],
    ) -> None:
        _require_non_empty("tape_id", tape_id)
        for record in records:
            _require_json_object("memory record", record)
        now = datetime.now(UTC)
        entries = [
            {
                "id": _new_entry_id(),
                "kind": "memory_record",
                "payload": record,
                "timestamp": now.timestamp(),
            }
            for record in records
        ]
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM tape_entries WHERE tape_id = ? AND kind = 'memory_record'",
                (tape_id,),
            )
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), -1) AS max_seq FROM tape_entries WHERE tape_id = ?",
                (tape_id,),
            ).fetchone()
            max_seq = _row_required_int(row, "max_seq", context="tape max seq")
            values = [
                (
                    tape_id,
                    max_seq + offset,
                    json.dumps(entry, sort_keys=True),
                    "memory_record",
                    None,
                    None,
                    None,
                    now.isoformat(),
                )
                for offset, entry in enumerate(entries, start=1)
            ]
            connection.executemany(
                """
                INSERT INTO tape_entries (
                    tape_id, seq, entry_json, kind, run_id, tool_call_id,
                    anchor_type, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )


class SQLiteCheckpointStore:
    _CREATE_SCHEMA_SQL: Final[str] = """
    CREATE TABLE IF NOT EXISTS checkpoints (
        checkpoint_id TEXT PRIMARY KEY,
        tape_id TEXT NOT NULL,
        session_id TEXT,
        entry_count INTEGER NOT NULL,
        window_start INTEGER NOT NULL,
        label TEXT,
        snapshot_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS checkpoints_tape_id_created_idx
        ON checkpoints (tape_id, created_at, checkpoint_id);
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(self._CREATE_SCHEMA_SQL)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    async def save(self, snapshot: CheckpointSnapshot) -> None:
        meta = snapshot.meta
        payload = _checkpoint_snapshot_to_payload(snapshot)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO checkpoints (
                    checkpoint_id, tape_id, session_id, entry_count, window_start,
                    label, snapshot_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(checkpoint_id)
                DO UPDATE SET
                    tape_id = excluded.tape_id,
                    session_id = excluded.session_id,
                    entry_count = excluded.entry_count,
                    window_start = excluded.window_start,
                    label = excluded.label,
                    snapshot_json = excluded.snapshot_json,
                    created_at = excluded.created_at
                """,
                (
                    meta.checkpoint_id,
                    meta.tape_id,
                    meta.session_id,
                    meta.entry_count,
                    meta.window_start,
                    meta.label,
                    json.dumps(payload, sort_keys=True),
                    meta.created_at.isoformat(),
                ),
            )

    async def load(self, checkpoint_id: str) -> CheckpointSnapshot | None:
        _require_non_empty("checkpoint_id", checkpoint_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
        if row is None:
            return None
        return _checkpoint_snapshot_from_payload(
            _json_object_from_text(row["snapshot_json"], context="checkpoint snapshot")
        )

    async def list_by_tape(self, tape_id: str) -> list[CheckpointMeta]:
        _require_non_empty("tape_id", tape_id)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT checkpoint_id, tape_id, session_id, entry_count, window_start,
                       label, created_at
                FROM checkpoints
                WHERE tape_id = ?
                ORDER BY created_at, checkpoint_id
                """,
                (tape_id,),
            ).fetchall()
        return [_checkpoint_meta_from_row(row) for row in rows]

    async def delete(self, checkpoint_id: str) -> None:
        _require_non_empty("checkpoint_id", checkpoint_id)
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            )


def _checkpoint_snapshot_to_payload(snapshot: CheckpointSnapshot) -> dict[str, Any]:
    meta = snapshot.meta
    return {
        "meta": {
            "checkpoint_id": meta.checkpoint_id,
            "tape_id": meta.tape_id,
            "session_id": meta.session_id,
            "entry_count": meta.entry_count,
            "window_start": meta.window_start,
            "created_at": meta.created_at.isoformat(),
            "label": meta.label,
        },
        "tape_entries": list(snapshot.tape_entries),
        "plugin_states": snapshot.plugin_states,
        "extra": snapshot.extra,
    }


def _checkpoint_snapshot_from_payload(payload: dict[str, Any]) -> CheckpointSnapshot:
    meta_raw = _required_dict(payload, "meta", context="checkpoint snapshot")
    entries_raw = payload.get("tape_entries")
    if not isinstance(entries_raw, list):
        raise TypeError("checkpoint snapshot must include list tape_entries")
    entries: list[dict[str, Any]] = []
    for item in entries_raw:
        if not isinstance(item, dict):
            raise TypeError("checkpoint snapshot tape_entries must contain objects")
        entries.append(cast(dict[str, Any], item))
    plugin_states = _required_dict(
        payload,
        "plugin_states",
        context="checkpoint snapshot",
    )
    extra = payload.get("extra", {})
    if not isinstance(extra, dict):
        raise TypeError("checkpoint snapshot extra must be a JSON object")
    return CheckpointSnapshot(
        meta=_checkpoint_meta_from_payload(meta_raw),
        tape_entries=tuple(entries),
        plugin_states=plugin_states,
        extra=cast(dict[str, Any], extra),
    )


def _checkpoint_meta_from_payload(payload: dict[str, Any]) -> CheckpointMeta:
    return CheckpointMeta(
        checkpoint_id=_required_str(
            payload, "checkpoint_id", context="checkpoint meta"
        ),
        tape_id=_required_str(payload, "tape_id", context="checkpoint meta"),
        session_id=_optional_str(payload, "session_id", context="checkpoint meta"),
        entry_count=_required_int(payload, "entry_count", context="checkpoint meta"),
        window_start=_required_int(payload, "window_start", context="checkpoint meta"),
        created_at=datetime.fromisoformat(
            _required_str(payload, "created_at", context="checkpoint meta")
        ),
        label=_optional_str(payload, "label", context="checkpoint meta"),
    )


def _checkpoint_meta_from_row(row: sqlite3.Row) -> CheckpointMeta:
    return CheckpointMeta(
        checkpoint_id=_row_required_str(
            row, "checkpoint_id", context="checkpoint meta"
        ),
        tape_id=_row_required_str(row, "tape_id", context="checkpoint meta"),
        session_id=_row_optional_str(row, "session_id", context="checkpoint meta"),
        entry_count=_row_required_int(row, "entry_count", context="checkpoint meta"),
        window_start=_row_required_int(row, "window_start", context="checkpoint meta"),
        created_at=datetime.fromisoformat(
            _row_required_str(row, "created_at", context="checkpoint meta")
        ),
        label=_row_optional_str(row, "label", context="checkpoint meta"),
    )


def _optional_entry_str(entry: dict[str, Any], key: str) -> str | None:
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"tape entry {key} must be a string when present")
    return value


def _entry_nested_str(entry: dict[str, Any], key: str) -> str | None:
    for container_key in ("meta", "payload"):
        container = entry.get(container_key)
        if not isinstance(container, dict):
            continue
        value = container.get(key)
        if isinstance(value, str) and value:
            return value
        if value is not None and not isinstance(value, str):
            raise TypeError(f"tape entry {container_key}.{key} must be a string")
    return None


def _entry_anchor_type(entry: dict[str, Any]) -> str | None:
    value = entry.get("anchor_type")
    if isinstance(value, str) and value:
        return value
    if value is not None and not isinstance(value, str):
        raise TypeError("tape entry anchor_type must be a string")
    meta = entry.get("meta")
    if not isinstance(meta, dict):
        return None
    meta_value = meta.get("anchor_type")
    if isinstance(meta_value, str) and meta_value:
        return meta_value
    if meta_value is not None and not isinstance(meta_value, str):
        raise TypeError("tape entry meta.anchor_type must be a string")
    return None


def _json_object_from_text(value: object, *, context: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise TypeError(f"{context} must include JSON text")
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise TypeError(f"{context} must decode to a JSON object")
    return cast(dict[str, Any], loaded)


def _required_dict(
    payload: dict[str, Any],
    key: str,
    *,
    context: str,
) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"{context} must include JSON object {key}")
    return cast(dict[str, Any], value)


def _required_str(payload: dict[str, Any], key: str, *, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{context} must include string {key}")
    return value


def _optional_str(
    payload: dict[str, Any],
    key: str,
    *,
    context: str,
) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{context} must include string or None {key}")
    return value


def _required_int(payload: dict[str, Any], key: str, *, context: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{context} must include int {key}")
    return value


def _row_required_str(row: sqlite3.Row | None, key: str, *, context: str) -> str:
    if row is None:
        raise TypeError(f"{context} row is missing")
    value = row[key]
    if not isinstance(value, str):
        raise TypeError(f"{context} row must include string {key}")
    return value


def _row_optional_str(row: sqlite3.Row, key: str, *, context: str) -> str | None:
    value = row[key]
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{context} row must include string or NULL {key}")
    return value


def _row_required_int(row: sqlite3.Row | None, key: str, *, context: str) -> int:
    if row is None:
        raise TypeError(f"{context} row is missing")
    value = row[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{context} row must include int {key}")
    return value


def _require_non_empty(field_name: str, value: str) -> None:
    if not value:
        raise ValueError(f"{field_name} must be non-empty")


def _require_json_object(field_name: str, value: object) -> None:
    if not isinstance(value, dict):
        raise TypeError(f"{field_name} must be a JSON object")


def _new_entry_id() -> str:
    import uuid

    return str(uuid.uuid4())


__all__ = ["SQLiteCheckpointStore", "SQLiteTapeStore"]
