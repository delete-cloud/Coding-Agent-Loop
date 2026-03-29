"""FileSessionStore — JSON file-based session persistence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class FileSessionStore:
    """File-based SessionStore implementation."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self._base_dir / f"{session_id}.json"

    async def save_session(self, session_id: str, data: dict[str, Any]) -> None:
        path = self._path(session_id)
        path.write_text(json.dumps(data, indent=2))

    async def load_session(self, session_id: str) -> dict[str, Any] | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        return json.loads(path.read_text())

    async def list_sessions(self) -> list[str]:
        return [p.stem for p in self._base_dir.glob("*.json")]

    async def delete_session(self, session_id: str) -> None:
        path = self._path(session_id)
        if path.exists():
            path.unlink()
