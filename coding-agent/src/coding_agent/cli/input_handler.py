"""Interactive input handling with prompt-toolkit.

Supports Enter-to-submit with Shift+Enter multi-line editing.

Bash mode toggle (Claude Code style):
- ! on empty buffer: instantly switch to shell mode
- Escape / Backspace on empty shell buffer: switch back to chat mode
"""

from __future__ import annotations

import time
from collections.abc import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import AnyFormattedText, FormattedText
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style

from coding_agent.cli.commands import get_command_completions
from coding_agent.cli.terminal_output import print_pt

_CTRLC_TIMEOUT = 2.0
_SHIFT_ENTER_SEQUENCE = (Keys.Escape, Keys.ControlJ)

for _sequence in ("\x1b[27;2;13~", "\x1b[13;2u"):
    ANSI_SEQUENCES[_sequence] = _SHIFT_ENTER_SEQUENCE

SWITCH_TO_SHELL = "__SWITCH_TO_SHELL__"
SWITCH_TO_CHAT = "__SWITCH_TO_CHAT__"


class SlashCommandCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text
        if text.startswith("/"):
            for cmd in get_command_completions():
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text))


PROMPT_STYLE = Style.from_dict(
    {
        "prompt": "bold cyan",
        "input": "white",
        "shell": "bold yellow",
        "shell.path": "yellow",
        "shell.hint": "dim",
    }
)


class InputHandler:
    def __init__(self):
        self.multiline = True
        self.bindings = KeyBindings()
        self._last_ctrlc: float = 0.0
        self._shell_mode = False
        self._setup_bindings()
        self.chat_history = InMemoryHistory()
        self.shell_history = InMemoryHistory()
        self.chat_session = PromptSession(
            completer=SlashCommandCompleter(),
            auto_suggest=AutoSuggestFromHistory(),
            history=self.chat_history,
            enable_history_search=True,
            style=PROMPT_STYLE,
            multiline=True,
            key_bindings=self.bindings,
            prompt_continuation=self._continuation_prompt,
        )
        self.shell_session = PromptSession(
            auto_suggest=AutoSuggestFromHistory(),
            history=self.shell_history,
            enable_history_search=True,
            style=PROMPT_STYLE,
            multiline=False,
            key_bindings=self.bindings,
        )

    @staticmethod
    def _continuation_prompt(width, line_number, is_soft_wrap):
        return ". " + " " * (width - 2)

    def _should_exit_on_ctrlc(self) -> bool:
        return time.monotonic() - self._last_ctrlc < _CTRLC_TIMEOUT

    def _simulate_ctrlc(self) -> None:
        self._last_ctrlc = time.monotonic()

    @property
    def shell_mode(self) -> bool:
        return self._shell_mode

    def exit_shell_mode(self) -> None:
        self._shell_mode = False

    def _setup_bindings(self):
        @self.bindings.add("c-c")
        def _(event):
            if self._should_exit_on_ctrlc():
                event.app.exit()
            else:
                self._simulate_ctrlc()
                event.app.current_buffer.reset()

                def _hint():
                    print_pt("\n(Press Ctrl+C again to exit)")

                run_in_terminal(_hint)

        @self.bindings.add("c-d")
        def _(event):
            event.app.exit()

        @self.bindings.add("enter")
        def _(event):
            event.app.current_buffer.validate_and_handle()

        @self.bindings.add("escape", "c-j")
        def _(event):
            event.app.current_buffer.insert_text("\n")

        @self.bindings.add("!", filter=Condition(lambda: not self._shell_mode))
        def _(event):
            buf = event.app.current_buffer
            if buf.text == "" and buf.cursor_position == 0:
                event.app.exit(result=SWITCH_TO_SHELL)
            else:
                buf.insert_text("!")

        @self.bindings.add(
            "escape", eager=True, filter=Condition(lambda: self._shell_mode)
        )
        def _(event):
            buf = event.app.current_buffer
            if buf.text == "":
                event.app.exit(result=SWITCH_TO_CHAT)

        @self.bindings.add("backspace", filter=Condition(lambda: self._shell_mode))
        def _(event):
            buf = event.app.current_buffer
            if buf.text == "":
                event.app.exit(result=SWITCH_TO_CHAT)
            else:
                buf.delete_before_cursor(1)

    def build_prompt(
        self,
        *,
        turn_count: int,
        shell_mode: bool = False,
        cwd: str | None = None,
    ) -> FormattedText:
        if shell_mode:
            location = cwd or "~"
            label = location.rstrip("/").split("/")[-1] or location
            return FormattedText(
                [
                    ("class:shell", "bash"),
                    ("class:shell.hint", " "),
                    ("class:shell.path", f"{label}"),
                    ("class:shell.hint", " $ "),
                ]
            )

        return FormattedText(
            [
                ("class:prompt", f"[{turn_count}]"),
                ("class:prompt", " > "),
            ]
        )

    async def get_input(
        self,
        prompt: AnyFormattedText | str = "> ",
        *,
        shell_mode: bool = False,
        prompt_builder: Callable[[bool], AnyFormattedText] | None = None,
    ) -> str | None:
        self._shell_mode = shell_mode
        while True:
            session = self.shell_session if self._shell_mode else self.chat_session
            current_prompt = (
                prompt_builder(self._shell_mode) if prompt_builder else prompt
            )
            try:
                result = await session.prompt_async(current_prompt)
            except (EOFError, KeyboardInterrupt):
                return None
            if result is None:
                return None
            if result == SWITCH_TO_SHELL:
                self._shell_mode = True
                continue
            if result == SWITCH_TO_CHAT:
                self._shell_mode = False
                continue
            return result.strip()
