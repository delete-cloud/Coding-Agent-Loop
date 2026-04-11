"""Interactive input handling with prompt-toolkit."""

from __future__ import annotations

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from coding_agent.cli.commands import get_command_completions


class SlashCommandCompleter(Completer):
    """Completer for slash commands."""
    
    def get_completions(self, document, complete_event):
        text = document.text
        if text.startswith('/'):
            for cmd in get_command_completions():
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text))


# Custom style for prompt
PROMPT_STYLE = Style.from_dict({
    'prompt': 'bold cyan',
    'input': 'white',
})


class InputHandler:
    """Handles interactive user input with history and completion."""
    
    def __init__(self):
        self.session = PromptSession(
            completer=SlashCommandCompleter(),
            auto_suggest=AutoSuggestFromHistory(),
            enable_history_search=True,
            style=PROMPT_STYLE,
        )
        self.bindings = KeyBindings()
        self._setup_bindings()
    
    def _setup_bindings(self):
        """Setup custom key bindings."""
        @self.bindings.add('c-c')
        def _(event):
            """Ctrl+C to cancel current input."""
            event.app.current_buffer.reset()
        
        @self.bindings.add('c-d')
        def _(event):
            """Ctrl+D to exit."""
            event.app.exit()
    
    async def get_input(self, prompt: str = "> ") -> str | None:
        """Get input from user.
        
        Returns:
            User input string, or None if user wants to exit
        """
        try:
            result = await self.session.prompt_async(
                prompt,
                key_bindings=self.bindings,
            )
            return result.strip()
        except (EOFError, KeyboardInterrupt):
            return None
