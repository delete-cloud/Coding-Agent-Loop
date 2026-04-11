"""Session management for persistent agent conversations."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from coding_agent.core.config import Config
from agentkit.tape.tape import Tape

logger = logging.getLogger(__name__)


class Session:
    """Manages a conversation session with persistence."""

    def __init__(
        self,
        session_id: str,
        tape: Tape,
        config: Config,
        status: Literal["active", "completed", "interrupted"] = "active",
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ):
        self.id = session_id
        self.tape = tape
        self.config = config
        self.status = status
        self.created_at = created_at or datetime.now()
        self.updated_at = updated_at or datetime.now()

    @classmethod
    def create(cls, config: Config) -> Session:
        """Create a new session with a fresh tape."""
        session_id = str(uuid.uuid4())[:8]
        config.tape_dir.mkdir(parents=True, exist_ok=True)
        tape = Tape(tape_id=session_id)
        return cls(session_id=session_id, tape=tape, config=config)

    @classmethod
    def load(cls, session_id: str, config: Config) -> Session | None:
        """Load an existing session from its tape file."""
        tape_path = config.tape_dir / f"{session_id}.jsonl"
        if not tape_path.exists():
            return None
        tape = Tape.load_jsonl(tape_path, tape_id=session_id)
        # Load metadata from session.json if exists
        meta_path = config.tape_dir / f"{session_id}.json"
        if meta_path.exists():
            try:
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
                return cls(
                    session_id=session_id,
                    tape=tape,
                    config=config,
                    status=meta.get("status", "active"),
                    created_at=datetime.fromisoformat(meta["created_at"]),
                    updated_at=datetime.fromisoformat(meta["updated_at"]),
                )
            except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                logger.warning(f"Failed to load session metadata for {session_id}: {e}")
        return cls(session_id=session_id, tape=tape, config=config)

    @property
    def tape_path(self) -> Path:
        return self.config.tape_dir / f"{self.id}.jsonl"

    def save_tape(self) -> None:
        self.config.tape_dir.mkdir(parents=True, exist_ok=True)
        self.tape.save_jsonl(self.tape_path)

    def save_metadata(self) -> None:
        """Save session metadata to disk."""
        meta_path = self.config.tape_dir / f"{self.id}.json"
        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "session_id": self.id,
                        "status": self.status,
                        "created_at": self.created_at.isoformat(),
                        "updated_at": self.updated_at.isoformat(),
                    },
                    f,
                    indent=2,
                )
        except (IOError, OSError) as e:
            logger.error(f"Failed to save session metadata: {e}")
            raise

    def update_activity(self) -> None:
        """Update last activity timestamp."""
        self.updated_at = datetime.now()
        self.save_metadata()

    def close(self, status: Literal["completed", "interrupted"] = "completed") -> None:
        """Close the session with final status."""
        self.status = status
        self.save_metadata()

    @classmethod
    def list_sessions(cls, config: Config) -> list[dict[str, object]]:
        """List all sessions in the tape directory."""
        sessions = []
        for meta_file in config.tape_dir.glob("*.json"):
            if meta_file.name.endswith(".jsonl"):
                continue
            try:
                with open(meta_file, encoding="utf-8") as f:
                    sessions.append(json.load(f))
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load session from {meta_file}: {e}")
                continue
        return sorted(sessions, key=lambda s: s["updated_at"], reverse=True)


class SessionRegistry:
    """In-memory registry of active sessions."""

    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def register(self, session: Session) -> None:
        """Register an active session."""
        self._sessions[session.id] = session

    def get(self, session_id: str) -> Session | None:
        """Get an active session by ID."""
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> None:
        """Remove a session from registry."""
        if session_id in self._sessions:
            del self._sessions[session_id]
