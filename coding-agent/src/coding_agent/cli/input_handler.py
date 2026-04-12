"""Interactive input handling with prompt-toolkit.

Supports Enter-to-submit with Shift+Enter multi-line editing.

Bash mode toggle (Claude Code style):
- ! on empty buffer: instantly switch to shell mode
- Escape / Backspace on empty shell buffer: switch back to chat mode
"""

from __future__ import annotations

import re as _re
import time
import uuid as _uuid
from collections.abc import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import (
    AnyFormattedText,
    FormattedText,
    StyleAndTextTuples,
)
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.processors import (
    Processor,
    Transformation,
    TransformationInput,
)
from prompt_toolkit.styles import Style

from coding_agent.cli.commands import (
    get_command_completions,
    get_commands_with_descriptions,
)
from coding_agent.cli.terminal_output import print_pt

_CTRLC_TIMEOUT = 2.0
_SHIFT_ENTER_SEQUENCE = (Keys.Escape, Keys.ControlJ)

for _sequence in ("\x1b[27;2;13~", "\x1b[13;2u"):
    ANSI_SEQUENCES[_sequence] = _SHIFT_ENTER_SEQUENCE

SWITCH_TO_SHELL = "__SWITCH_TO_SHELL__"
SWITCH_TO_CHAT = "__SWITCH_TO_CHAT__"
_SLASH_TOOLBAR_LIMIT = 5
_SLASH_TOOLBAR_DESCRIPTION_WIDTH = 24


def _truncate_toolbar_description(description: str) -> str:
    if len(description) <= _SLASH_TOOLBAR_DESCRIPTION_WIDTH:
        return description
    return description[:_SLASH_TOOLBAR_DESCRIPTION_WIDTH] + "…"


def _paste_placeholder_span(text: str, cursor_position: int) -> tuple[int, int] | None:
    for match in _PASTE_RE.finditer(text):
        start, end = match.span()
        if start < cursor_position <= end:
            return start, end
    return None


def _paste_placeholder_at_cursor(
    text: str, cursor_position: int
) -> tuple[int, int] | None:
    for match in _PASTE_RE.finditer(text):
        start, end = match.span()
        if start <= cursor_position < end:
            return start, end
    return None


def _previous_placeholder_span(
    text: str, cursor_position: int
) -> tuple[int, int] | None:
    previous: tuple[int, int] | None = None
    for match in _PASTE_RE.finditer(text):
        start, end = match.span()
        if start < cursor_position <= end:
            return start, end
        if end <= cursor_position:
            previous = (start, end)
            continue
        break
    return previous


def _next_placeholder_span(text: str, cursor_position: int) -> tuple[int, int] | None:
    for match in _PASTE_RE.finditer(text):
        start, end = match.span()
        if start <= cursor_position < end:
            return start, end
        if start > cursor_position:
            return start, end
    return None


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
        "paste.placeholder": "bg:#264653 #f1fa8c bold",
        "separator": "dim",
        "shell": "bold yellow",
        "shell.path": "yellow",
        "shell.hint": "dim",
        "toolbar": "bg:#333333 #bbbbbb",
        "toolbar.key": "bg:#333333 bold #ffffff",
        "toolbar.key.bestmatch": "bg:#333333 bold #ffff00",
    }
)


class PastePlaceholderProcessor(Processor):
    def apply_transformation(
        self, transformation_input: TransformationInput
    ) -> Transformation:
        text = transformation_input.document.lines[transformation_input.lineno]
        fragments = _highlight_paste_placeholder_fragments(text)
        return Transformation(fragments=fragments)


def _highlight_paste_placeholder_fragments(text: str) -> StyleAndTextTuples:
    fragments: StyleAndTextTuples = []
    last_end = 0
    for match in _PASTE_RE.finditer(text):
        start, end = match.span()
        if start > last_end:
            fragments.append(("class:input", text[last_end:start]))
        fragments.append(("class:paste.placeholder", match.group(0)))
        last_end = end
    if last_end < len(text):
        fragments.append(("class:input", text[last_end:]))
    if not fragments:
        fragments.append(("class:input", text))
    return fragments


class InputHandler:
    def __init__(self):
        self.multiline = True
        self.bindings = KeyBindings()
        self._last_ctrlc: float = 0.0
        self._shell_mode = False
        self._paste_refs: dict[str, str] = {}
        self._paste_counter: int = 0
        self._status_text: str = ""
        self._setup_bindings()
        self.chat_history = InMemoryHistory()
        self.shell_history = InMemoryHistory()
        self.chat_session = PromptSession(
            completer=SlashCommandCompleter(),
            auto_suggest=AutoSuggestFromHistory(),
            history=self.chat_history,
            enable_history_search=True,
            input_processors=[PastePlaceholderProcessor()],
            style=PROMPT_STYLE,
            multiline=True,
            key_bindings=self.bindings,
            prompt_continuation=self._continuation_prompt,
            bottom_toolbar=self._build_bottom_toolbar,
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

    def _chat_buffer_has_paste_placeholder(self) -> bool:
        return (
            _paste_placeholder_span(
                self.chat_session.default_buffer.text,
                self.chat_session.default_buffer.cursor_position,
            )
            is not None
        )

    def _highlight_paste_placeholders(self, text: str) -> FormattedText:
        return FormattedText(list(_highlight_paste_placeholder_fragments(text)))

    def _move_cursor_out_of_placeholder(self, buf, *, prefer_end: bool = True) -> None:
        span = _paste_placeholder_at_cursor(buf.text, buf.cursor_position)
        if span is None:
            return
        start, end = span
        buf.cursor_position = end if prefer_end else start

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

        @self.bindings.add(
            "escape",
            eager=True,
            filter=Condition(
                lambda: (
                    not self._shell_mode and self._chat_buffer_has_paste_placeholder()
                )
            ),
        )
        def _(event):
            buf = event.app.current_buffer
            span = _paste_placeholder_span(buf.text, buf.cursor_position)
            if span is None:
                return
            start, end = span
            if buf.cursor_position < end:
                buf.delete(end - buf.cursor_position)
            delete_before = buf.cursor_position - start
            if delete_before > 0:
                buf.delete_before_cursor(delete_before)

        @self.bindings.add("backspace", filter=Condition(lambda: self._shell_mode))
        def _(event):
            buf = event.app.current_buffer
            if buf.text == "":
                event.app.exit(result=SWITCH_TO_CHAT)
            else:
                buf.delete_before_cursor(1)

        @self.bindings.add("backspace", filter=Condition(lambda: not self._shell_mode))
        def _(event):
            buf = event.app.current_buffer
            span = _paste_placeholder_span(buf.text, buf.cursor_position)
            if span is not None:
                start, end = span
                if buf.cursor_position < end:
                    buf.delete(end - buf.cursor_position)
                delete_before = buf.cursor_position - start
                if delete_before > 0:
                    buf.delete_before_cursor(delete_before)
                return
            if buf.cursor_position > 0:
                buf.delete_before_cursor(1)

        @self.bindings.add(Keys.Any, filter=Condition(lambda: not self._shell_mode))
        def _(event):
            buf = event.app.current_buffer
            self._move_cursor_out_of_placeholder(buf)
            data = event.data
            if data:
                buf.insert_text(data)

        @self.bindings.add("delete", filter=Condition(lambda: not self._shell_mode))
        def _(event):
            buf = event.app.current_buffer
            span = _paste_placeholder_at_cursor(buf.text, buf.cursor_position)
            if span is not None:
                start, end = span
                if buf.cursor_position < end:
                    buf.delete(end - buf.cursor_position)
                delete_before = buf.cursor_position - start
                if delete_before > 0:
                    buf.delete_before_cursor(delete_before)
                return
            if buf.cursor_position < len(buf.text):
                buf.delete(1)

        @self.bindings.add("left", filter=Condition(lambda: not self._shell_mode))
        def _(event):
            buf = event.app.current_buffer
            span = _previous_placeholder_span(buf.text, buf.cursor_position)
            if span is not None:
                start, end = span
                if start < buf.cursor_position <= end:
                    buf.cursor_position = start
                    return
            if buf.cursor_position > 0:
                buf.cursor_position -= 1

        @self.bindings.add("right", filter=Condition(lambda: not self._shell_mode))
        def _(event):
            buf = event.app.current_buffer
            span = _next_placeholder_span(buf.text, buf.cursor_position)
            if span is not None:
                start, end = span
                if start <= buf.cursor_position < end:
                    buf.cursor_position = end
                    return
            if buf.cursor_position < len(buf.text):
                buf.cursor_position += 1

        @self.bindings.add(
            Keys.BracketedPaste, filter=Condition(lambda: not self._shell_mode)
        )
        def _(event):
            pasted = event.data
            self._paste_counter += 1
            folded, refs = fold_pasted_content(pasted, ref_id=str(self._paste_counter))
            if refs:
                self._paste_refs.update(refs)
            event.app.current_buffer.insert_text(folded)

    def _build_bottom_toolbar(self) -> FormattedText:
        if (
            not self.chat_session.default_buffer.text.startswith("/")
            and self._status_text
        ):
            return FormattedText(
                [
                    ("class:toolbar.key", " status "),
                    ("class:toolbar", f"{self._status_text}"),
                ]
            )
        return self._build_slash_toolbar(self.chat_session.default_buffer.text)

    def set_status_text(self, text: str) -> None:
        self._status_text = text

    def _build_slash_toolbar(self, text: str) -> FormattedText:
        if not text.startswith("/"):
            return FormattedText([])

        commands_with_desc = get_commands_with_descriptions()
        matches = [
            (cmd, desc) for cmd, desc in commands_with_desc if cmd.startswith(text)
        ]

        has_partial_input = text != "/"
        showing_matches = has_partial_input and bool(matches)

        visible_commands = matches if showing_matches else commands_with_desc
        visible_commands = visible_commands[:_SLASH_TOOLBAR_LIMIT]

        show_best_match = showing_matches

        fragments: list[tuple[str, str]] = [
            ("class:toolbar.key", " / "),
            ("class:toolbar", "Available commands: "),
        ]

        for index, (command, desc) in enumerate(visible_commands):
            if index > 0:
                fragments.append(("class:toolbar", "  "))

            is_best_match = index == 0 and show_best_match
            key_class = (
                "class:toolbar.key.bestmatch" if is_best_match else "class:toolbar.key"
            )

            fragments.append((key_class, command))
            if desc:
                fragments.append(
                    (
                        "class:toolbar",
                        f" - {_truncate_toolbar_description(desc)}",
                    )
                )

        return FormattedText(fragments)

    def build_prompt(
        self,
        *,
        turn_count: int,
        shell_mode: bool = False,
        cwd: str | None = None,
    ) -> FormattedText:
        separator = [("class:separator", "─" * 50 + "\n")]
        if shell_mode:
            location = cwd or "~"
            label = location.rstrip("/").split("/")[-1] or location
            return FormattedText(
                separator
                + [
                    ("class:shell", "bash"),
                    ("class:shell.hint", " "),
                    ("class:shell.path", f"{label}"),
                    ("class:shell.hint", " $ "),
                ]
            )

        return FormattedText(
            separator
            + [
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


_PASTE_RE = _re.compile(r"\[Pasted text #(\S+) \+\d+ lines\]")
_INLINE_LENGTH_THRESHOLD = 2000


def _make_paste_placeholder(ref_id: str, text: str) -> str:
    line_count = text.count("\n") + 1
    return f"[Pasted text #{ref_id} +{line_count} lines]"


def _should_fold_block(text: str, threshold: int) -> bool:
    line_count = text.count("\n") + 1
    return line_count > threshold or len(text) >= _INLINE_LENGTH_THRESHOLD


def fold_pasted_content(
    text: str, threshold: int = 20, *, ref_id: str | None = None
) -> tuple[str, dict[str, str]]:
    refs: dict[str, str] = {}
    if not _should_fold_block(text, threshold):
        return text, refs

    if ref_id is None:
        ref_id = _uuid.uuid4().hex[:8]
    refs[ref_id] = text
    return _make_paste_placeholder(ref_id, text), refs


def expand_pasted_refs(text: str, refs: dict[str, str]) -> str:
    if not refs:
        return text

    def _replace(m: _re.Match[str]) -> str:
        ref_id = m.group(1)
        replacement = refs.get(ref_id)
        if replacement is None:
            return m.group(0)
        return replacement

    return _PASTE_RE.sub(_replace, text)
