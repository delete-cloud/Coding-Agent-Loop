"""Tests for session management module."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from coding_agent.core.config import Config
from coding_agent.core.session import Session, SessionRegistry
from coding_agent.core.tape import Tape


class TestSession:
    """Test cases for Session class."""
    
    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for tests."""
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)
    
    @pytest.fixture
    def config(self, temp_dir):
        """Create a test configuration."""
        return Config(tape_dir=temp_dir, max_tokens=1000, api_key="test-key")
    
    def test_session_create(self, config):
        """Test creating a new session."""
        session = Session.create(config)
        
        assert session.id is not None
        assert len(session.id) == 8  # UUID truncated to 8 chars
        assert session.status == "active"
        assert isinstance(session.tape, Tape)
        assert session.config == config
        assert isinstance(session.created_at, datetime)
        assert isinstance(session.updated_at, datetime)
    
    def test_session_load_existing(self, config):
        """Test loading an existing session."""
        # Create a session first
        session = Session.create(config)
        session.save_metadata()
        
        # Also need tape file to exist for loading
        from coding_agent.core.tape import Entry
        session.tape.append(Entry.event("test", {"data": "value"}))
        
        # Load it back
        loaded = Session.load(session.id, config)
        
        assert loaded is not None
        assert loaded.id == session.id
        assert loaded.status == session.status
        assert isinstance(loaded.tape, Tape)
    
    def test_session_load_nonexistent(self, config):
        """Test loading a non-existent session returns None."""
        loaded = Session.load("nonexistent", config)
        assert loaded is None
    
    def test_session_load_without_metadata(self, config):
        """Test loading a session without metadata file."""
        # Create a session but don't save metadata
        session = Session.create(config)
        # Just create the tape file, not the metadata
        from coding_agent.core.tape import Entry
        session.tape.append(Entry.event("test", {"data": "value"}))
        
        # Load should still work with defaults
        loaded = Session.load(session.id, config)
        
        assert loaded is not None
        assert loaded.id == session.id
        assert loaded.status == "active"  # Default status
    
    def test_save_metadata(self, config):
        """Test saving session metadata."""
        session = Session.create(config)
        session.save_metadata()
        
        meta_path = config.tape_dir / f"{session.id}.json"
        assert meta_path.exists()
        
        with open(meta_path) as f:
            data = json.load(f)
        
        assert data["session_id"] == session.id
        assert data["status"] == "active"
        assert "created_at" in data
        assert "updated_at" in data
    
    def test_update_activity(self, config):
        """Test updating session activity timestamp."""
        session = Session.create(config)
        old_updated_at = session.updated_at
        
        # Wait a tiny bit to ensure time difference
        import time
        time.sleep(0.01)
        
        session.update_activity()
        
        assert session.updated_at > old_updated_at
        
        # Verify metadata was saved
        meta_path = config.tape_dir / f"{session.id}.json"
        assert meta_path.exists()
    
    def test_close_session(self, config):
        """Test closing a session."""
        session = Session.create(config)
        session.close("completed")
        
        assert session.status == "completed"
        
        meta_path = config.tape_dir / f"{session.id}.json"
        with open(meta_path) as f:
            data = json.load(f)
        assert data["status"] == "completed"
    
    def test_close_session_interrupted(self, config):
        """Test closing a session with interrupted status."""
        session = Session.create(config)
        session.close("interrupted")
        
        assert session.status == "interrupted"
    
    def test_list_sessions(self, config):
        """Test listing all sessions."""
        # Create multiple sessions
        session1 = Session.create(config)
        session1.close("completed")
        
        import time
        time.sleep(0.01)
        
        session2 = Session.create(config)
        session2.close("interrupted")
        
        sessions = Session.list_sessions(config)
        
        assert len(sessions) == 2
        # Should be sorted by updated_at desc
        assert sessions[0]["session_id"] == session2.id
        assert sessions[1]["session_id"] == session1.id
    
    def test_list_sessions_empty(self, config):
        """Test listing sessions when none exist."""
        sessions = Session.list_sessions(config)
        assert sessions == []
    
    def test_list_sessions_ignores_jsonl(self, config):
        """Test that list_sessions ignores .jsonl files."""
        # Create a session
        session = Session.create(config)
        session.save_metadata()
        
        # Create a .jsonl file that shouldn't be counted
        jsonl_path = config.tape_dir / "test.jsonl"
        jsonl_path.write_text('{"test": "data"}\n')
        
        sessions = Session.list_sessions(config)
        assert len(sessions) == 1


class TestSessionRegistry:
    """Test cases for SessionRegistry class."""
    
    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for tests."""
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)
    
    @pytest.fixture
    def config(self, temp_dir):
        """Create a test configuration."""
        return Config(tape_dir=temp_dir, max_tokens=1000, api_key="test-key")
    
    def test_register_session(self, config):
        """Test registering a session."""
        registry = SessionRegistry()
        session = Session.create(config)
        
        registry.register(session)
        
        assert registry.get(session.id) is session
    
    def test_get_nonexistent_session(self):
        """Test getting a non-existent session."""
        registry = SessionRegistry()
        
        result = registry.get("nonexistent")
        
        assert result is None
    
    def test_remove_session(self, config):
        """Test removing a session from registry."""
        registry = SessionRegistry()
        session = Session.create(config)
        registry.register(session)
        
        registry.remove(session.id)
        
        assert registry.get(session.id) is None
    
    def test_remove_nonexistent_session(self):
        """Test removing a non-existent session doesn't raise error."""
        registry = SessionRegistry()
        
        # Should not raise
        registry.remove("nonexistent")
    
    def test_multiple_sessions(self, config):
        """Test managing multiple sessions."""
        registry = SessionRegistry()
        session1 = Session.create(config)
        session2 = Session.create(config)
        
        registry.register(session1)
        registry.register(session2)
        
        assert registry.get(session1.id) is session1
        assert registry.get(session2.id) is session2
        
        registry.remove(session1.id)
        
        assert registry.get(session1.id) is None
        assert registry.get(session2.id) is session2
