"""Tests for REPL functionality."""

import pytest

from coding_agent.cli.input_handler import InputHandler


class TestInputHandler:
    def test_input_handler_creation(self):
        handler = InputHandler()
        assert handler is not None
        assert handler.session is not None
    
    @pytest.mark.asyncio
    async def test_get_input_mock(self, monkeypatch):
        """Test input with mocked prompt."""
        handler = InputHandler()
        
        # Mock the prompt_async to return test input
        async def mock_prompt(*args, **kwargs):
            return "test input"
        
        monkeypatch.setattr(handler.session, 'prompt_async', mock_prompt)
        
        result = await handler.get_input()
        assert result == "test input"
    
    @pytest.mark.asyncio
    async def test_get_input_with_custom_prompt(self, monkeypatch):
        """Test input with custom prompt."""
        handler = InputHandler()
        
        async def mock_prompt(prompt, **kwargs):
            return f"received: {prompt}"
        
        monkeypatch.setattr(handler.session, 'prompt_async', mock_prompt)
        
        result = await handler.get_input(prompt="[0] >")
        # Result is stripped of trailing whitespace
        assert result == "received: [0] >"
    
    @pytest.mark.asyncio
    async def test_get_input_strips_whitespace(self, monkeypatch):
        """Test that input is properly stripped."""
        handler = InputHandler()
        
        async def mock_prompt(*args, **kwargs):
            return "  input with spaces  "
        
        monkeypatch.setattr(handler.session, 'prompt_async', mock_prompt)
        
        result = await handler.get_input()
        assert result == "input with spaces"
    
    @pytest.mark.asyncio
    async def test_get_input_eof_error(self, monkeypatch):
        """Test handling of EOFError (Ctrl+D)."""
        handler = InputHandler()
        
        async def mock_prompt(*args, **kwargs):
            raise EOFError()
        
        monkeypatch.setattr(handler.session, 'prompt_async', mock_prompt)
        
        result = await handler.get_input()
        assert result is None
    
    @pytest.mark.asyncio
    async def test_get_input_keyboard_interrupt(self, monkeypatch):
        """Test handling of KeyboardInterrupt (Ctrl+C)."""
        handler = InputHandler()
        
        async def mock_prompt(*args, **kwargs):
            raise KeyboardInterrupt()
        
        monkeypatch.setattr(handler.session, 'prompt_async', mock_prompt)
        
        result = await handler.get_input()
        assert result is None
    
    def test_key_bindings_exist(self):
        """Test that key bindings are set up."""
        handler = InputHandler()
        assert handler.bindings is not None


class TestREPLImports:
    """Test that REPL module imports work correctly."""
    
    def test_repl_module_imports(self):
        """Test that repl module can be imported."""
        from coding_agent.cli.repl import InteractiveSession, run_repl
        assert InteractiveSession is not None
        assert run_repl is not None
    
    def test_repl_session_creation_requires_config(self):
        """Test that InteractiveSession requires a config."""
        from coding_agent.cli.repl import InteractiveSession
        
        # Should raise TypeError without config
        with pytest.raises(TypeError):
            InteractiveSession()
