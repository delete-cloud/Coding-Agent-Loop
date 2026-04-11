# Python Agent TUI Research

Research on terminal user interface patterns for Python coding agents, covering frameworks, REPL loops, streaming display, tool call UX, and production implementations.

---

## 1. Python TUI Framework Landscape

### The Three Libraries That Matter

| Library | Role | Used By |
|---------|------|---------|
| **`prompt_toolkit`** | Input handling, REPL loop, key bindings, completions | Aider, gptme, TermTalk |
| **`rich`** | Output rendering — Markdown, syntax highlighting, tables, progress | Aider, gptme, PydanticAI |
| **`textual`** | Full-screen TUI framework (built on Rich) | Some newer projects, not mainstream for agents |

### Why This Stack Won

**`prompt_toolkit`** dominates input because:
- `PromptSession` with `prompt_async()` — non-blocking async input that works with `asyncio`
- Built-in `FileHistory`, tab completion (`Completer`), multiline editing
- VI and Emacs editing modes (`EditingMode.VI`)
- Custom `KeyBindings` for agent-specific shortcuts
- `PygmentsLexer(MarkdownLexer)` for syntax-highlighted input

**`rich`** dominates output because:
- `Console.print()` with `Markdown()` renders streaming LLM output beautifully
- `Text()` objects with color styling for tool output, errors, warnings
- Automatic terminal width handling and NO_COLOR support
- `rich.live.Live` for in-place updating displays (spinners, progress)

**`textual` is NOT used by production agents** — it takes over the full terminal viewport (losing scrollback/search), and coding agents are fundamentally linear chat interfaces that work better with native terminal scrolling. This insight comes directly from pi-tui's author (Mario Zechner): "Coding agents have this nice property that they're basically a chat interface... everything is nicely linear, which lends itself well to working with the native terminal emulator."

### The Two TUI Philosophies

| Approach | Description | Used By |
|----------|-------------|---------|
| **Scrollback-based** ("append to terminal") | Write output to scrollback buffer, only redraw spinners/input area | Claude Code, Codex, Droid, Aider, gptme, pi |
| **Full-screen** ("own the viewport") | Take over terminal, simulate scrolling, custom search | Amp, OpenCode, Crush |

**For a Python coding agent, scrollback-based is the clear winner.** You get native terminal scrolling, search, copy-paste, mouse support for free. Full-screen is harder to implement and loses terminal features users expect.

---

## 2. REPL Loop Patterns

### Pattern A: Synchronous REPL (Aider)

Aider uses a synchronous blocking pattern — the simplest approach:

```python
# Simplified from aider/coders/base_coder.py
class Coder:
    def run(self, with_message=None):
        while True:
            try:
                if with_message:
                    inp = with_message
                    with_message = None
                else:
                    inp = self.io.get_input(...)  # blocks here (prompt_toolkit)
                
                if inp.startswith("/"):
                    self.commands.run(inp)
                    continue
                
                self.send_message(inp)  # calls LLM, streams response
            except KeyboardInterrupt:
                continue
            except EOFError:
                break
```

Key details:
- `io.get_input()` uses `prompt_toolkit.PromptSession.prompt()` (blocking)
- Streaming happens inline during `send_message()` — no concurrent input
- File watcher interrupts via `self.io.interrupt_input()` which calls `prompt_session.app.exit()`
- Simple, no asyncio needed, works reliably

### Pattern B: Async REPL (gptme, TermTalk)

gptme and TermTalk use `asyncio` with `prompt_async()`:

```python
# Simplified from TermTalk pattern
async def interactive_loop(model, shutdown_event):
    session = PromptSession(
        history=InMemoryHistory(),
        completer=command_completer,
        multiline=True,
        vi_mode=True,
        bottom_toolbar=get_toolbar,
    )
    
    while not shutdown_event.is_set():
        message = await session.prompt_async("> ", refresh_interval=0.05)
        
        if message.startswith("/"):
            handle_command(message)
            continue
        
        output = await send_to_llm(model, message)
        print_formatted_text(FormattedText([('class:assistant', f"LLM: {output}")]))

# Main orchestration
async def main():
    shutdown_event = asyncio.Event()
    interactive_task = asyncio.create_task(interactive_loop(...))
    # Can run other tasks concurrently (TCP server, file watcher, etc.)
    await asyncio.gather(interactive_task, ...)
```

Key details:
- `prompt_async()` doesn't block the event loop
- Can run background tasks (file watchers, notification servers) concurrently
- `shutdown_event` coordinates graceful cleanup
- Signal handlers: `loop.add_signal_handler(signal.SIGINT, shutdown_event.set)`

### Pattern C: PydanticAI's Built-in CLI

PydanticAI provides `agent.to_cli_sync()` which wraps the entire REPL:

```python
agent = Agent(model=model, instructions=instructions)
agent.to_cli_sync()  # One-liner REPL
```

This is the simplest possible approach — good for prototyping but not customizable enough for a production coding agent.

### Recommended: Async REPL with prompt_toolkit

```python
import asyncio
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout

async def repl_loop(agent):
    session = PromptSession(history=FileHistory(".agent_history"))
    
    while True:
        try:
            with patch_stdout():  # Allows background output during input
                user_input = await session.prompt_async("❯ ")
        except EOFError:
            break
        except KeyboardInterrupt:
            continue
        
        if user_input.startswith("/"):
            await handle_command(user_input)
            continue
        
        await agent.run(user_input)  # Streams output via callbacks
```

The `patch_stdout()` context manager is critical — it allows background tasks to print output without corrupting the prompt display.

---

## 3. Streaming Display Patterns

### Pattern A: Aider's MarkdownStream

Aider has a custom `MarkdownStream` class that incrementally renders streaming Markdown:

```python
# From aider/io.py (simplified)
class InputOutput:
    def assistant_output(self, message, pretty=None):
        if pretty:
            show_resp = Markdown(message, 
                                 style=self.assistant_output_color,
                                 code_theme=self.code_theme)
        else:
            show_resp = Text(message)
        self.console.print(show_resp)
```

For streaming, Aider uses `mdstream.MarkdownStream` which:
1. Buffers incoming tokens
2. Periodically re-renders the partial Markdown using `rich.live.Live`
3. Uses `\r` + cursor movement to update in-place
4. On completion, does a final render of the complete Markdown

### Pattern B: Simple Token Streaming (print + flush)

The simplest approach, used by many lightweight agents:

```python
import sys

async for chunk in llm.stream(messages):
    token = chunk.choices[0].delta.content
    if token:
        print(token, end="", flush=True)

print()  # Final newline
```

This works but produces ugly output (no Markdown rendering, no syntax highlighting).

### Pattern C: Rich Live Display (Recommended)

```python
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown

console = Console()

async def stream_response(response_stream):
    accumulated = ""
    
    with Live(console=console, refresh_per_second=10, vertical_overflow="visible") as live:
        async for chunk in response_stream:
            token = chunk.delta.content or ""
            accumulated += token
            live.update(Markdown(accumulated))
    
    # Final render (Live might not have caught the last update)
    console.print(Markdown(accumulated))
```

Key considerations:
- `refresh_per_second=10` throttles re-renders to prevent flicker
- `vertical_overflow="visible"` allows the output to grow beyond viewport
- After `Live` exits, do a final `console.print()` for the scrollback buffer
- Rich's `Markdown` handles code blocks, bold, italic, lists automatically

### Pattern D: Differential Rendering (pi-tui approach)

pi-tui (the Pi coding agent) uses a more sophisticated approach:
1. Components cache their rendered output (array of styled strings)
2. On update, compare new lines against previous backbuffer
3. Only redraw from the first changed line downward
4. Wrap all rendering in **synchronized output** escape sequences (`CSI ?2026h` / `CSI ?2026l`) for flicker-free atomic updates

This is more complex but produces the smoothest output. For a Python implementation, this would be:

```python
import sys

def atomic_write(content: str):
    """Write content atomically using synchronized output."""
    sys.stdout.write("\033[?2026h")  # Begin synchronized update
    sys.stdout.write(content)
    sys.stdout.write("\033[?2026l")  # End synchronized update
    sys.stdout.flush()
```

---

## 4. Tool Call UX Patterns

### Pattern A: Inline Rendering (Claude Code / Aider style)

Tool calls are rendered inline in the conversation flow with visual distinction:

```
❯ fix the failing test

⏳ Running tool: read_file
   path: tests/test_auth.py

✅ Read 45 lines from tests/test_auth.py

⏳ Running tool: edit_file  
   path: src/auth.py
   
   - old: def validate(token):
   + new: def validate(token: str) -> bool:

? Apply this edit? (Y/n): y
✅ Applied edit to src/auth.py

⏳ Running tool: bash
   command: python -m pytest tests/test_auth.py

✅ All 3 tests passed
```

Implementation with Rich:

```python
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

console = Console()

def render_tool_call(tool_name: str, args: dict):
    """Render a tool call with a spinner."""
    header = Text(f"⏳ Running: {tool_name}", style="bold yellow")
    console.print(header)
    for k, v in args.items():
        console.print(f"   {k}: {v}", style="dim")

def render_tool_result(tool_name: str, result: str, success: bool):
    """Render tool result."""
    icon = "✅" if success else "❌"
    style = "green" if success else "red"
    console.print(f"{icon} {tool_name}: {result}", style=style)
```

### Pattern B: Collapsible Sections (gptme style)

gptme renders tool calls as code blocks that the LLM produces, then executes them and shows output:

```
Assistant: I'll read the test file to understand the failure.

```shell
cat tests/test_auth.py
```

System: [executed, exit code 0]
\```
def test_login():
    ...
\```

### Pattern C: Approval Flows

All production agents implement tool approval. The key patterns:

**Aider's approach** — `confirm_ask()` with group support:
```python
def confirm_ask(self, question, default="y", subject=None, 
                explicit_yes_required=False, group=None, allow_never=False):
    # Supports: Yes/No/All/Skip all/Don't ask again
    # --yes flag auto-approves everything
    # Group-based: approve all file edits at once
    if self.yes is True:
        return not explicit_yes_required
    res = self.prompt_session.prompt(question, style=style)
```

**Copilot CLI's approach** — tiered permission levels:
- Per-operation approval (default)
- Session-wide tool approval
- Pre-approved tools via `--allow-tool=git`
- Denied tools via `--deny-tool=rm`

**Recommended pattern** for a Python agent:
```python
class ApprovalPolicy:
    AUTO_APPROVE = "auto"      # Read operations, safe tools
    CONFIRM = "confirm"        # File edits, shell commands
    ALWAYS_ASK = "always_ask"  # Destructive operations

TOOL_POLICIES = {
    "read_file": ApprovalPolicy.AUTO_APPROVE,
    "edit_file": ApprovalPolicy.CONFIRM,
    "bash": ApprovalPolicy.CONFIRM,
    "delete_file": ApprovalPolicy.ALWAYS_ASK,
}
```

---

## 5. Amp-Specific Patterns

Amp's CLI is not open-source, but from the existing architecture documents in this project and pi-tui's analysis:

### Full-Screen TUI Approach
- Amp uses a **full-screen TUI** (like OpenCode), taking ownership of the terminal viewport
- This means custom scrolling, custom search, and loss of native scrollback buffer

### Contrast with Scrollback Approach
From the pi-tui blog post:
> "Amp and opencode use [the full-screen] approach... Claude Code, Codex, and Droid [use the scrollback approach]."

The full-screen approach enables split panes, status bars, and command palettes. But loses native terminal scrolling, search, and simplicity.

### Event Streaming Architecture
From the OpenCode architecture doc (similar pattern to Amp):
- Backend sends **SSE (Server-Sent Events)** to the TUI
- Events: `text_delta`, `tool_call_start`, `tool_call_result`, `error`
- TUI subscribes to event streams and updates reactively
- Decouples the agent loop from the display layer

---

## 6. Recommended Patterns for a Python Coding Agent

### Architecture Overview

```
┌─────────────────────────────────────────────────┐
│                   REPL Loop                      │
│  ┌─────────────┐    ┌────────────────────────┐  │
│  │ prompt_toolkit│    │   Agent Loop (async)   │  │
│  │  Input Layer │◄──►│  LLM + Tool Execution  │  │
│  └─────────────┘    └────────────────────────┘  │
│         │                      │                 │
│         ▼                      ▼                 │
│  ┌─────────────────────────────────────────┐    │
│  │         Display Layer (Rich)             │    │
│  │  • Streaming Markdown (Live)             │    │
│  │  • Tool call rendering (Panel/Text)      │    │
│  │  • Approval prompts (prompt_toolkit)     │    │
│  │  • Status/spinners (Status/Spinner)      │    │
│  └─────────────────────────────────────────┘    │
└─────────────────────────────────────────────────┘
```

### Recommended Library Stack

```toml
[project]
dependencies = [
    "prompt-toolkit>=3.0",   # Input handling, REPL
    "rich>=13.0",            # Output rendering
    "pygments>=2.0",         # Syntax highlighting in input
]
```

Do NOT use: `textual` (overkill for linear chat), `blessed`/`curses` (too low-level), `click` (not designed for REPLs).

### Key Implementation Patterns

#### 1. InputOutput Separation (follow Aider's pattern)

```python
class AgentIO:
    def __init__(self, pretty=True, yes=False):
        self.console = Console()
        self.session = PromptSession(
            history=FileHistory(".agent_history"),
            lexer=PygmentsLexer(MarkdownLexer),
        )
        self.yes = yes  # Auto-approve mode

    async def get_input(self, prompt="❯ ") -> str:
        return await self.session.prompt_async(prompt)

    def tool_output(self, msg, style=None):
        self.console.print(msg, style=style)

    def tool_error(self, msg):
        self.console.print(f"[red]✗[/red] {msg}")

    async def stream_markdown(self, token_stream):
        accumulated = ""
        with Live(console=self.console, refresh_per_second=8) as live:
            async for token in token_stream:
                accumulated += token
                live.update(Markdown(accumulated))
```

#### 2. Event-Driven Agent Display

```python
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator

class EventType(Enum):
    TEXT_DELTA = "text_delta"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_RESULT = "tool_call_result"
    THINKING = "thinking"
    ERROR = "error"
    DONE = "done"

@dataclass
class AgentEvent:
    type: EventType
    data: dict

async def render_agent_events(io: AgentIO, events: AsyncIterator[AgentEvent]):
    text_buffer = ""
    live = None

    async for event in events:
        match event.type:
            case EventType.TEXT_DELTA:
                text_buffer += event.data["text"]
                if live is None:
                    live = Live(console=io.console, refresh_per_second=8)
                    live.start()
                live.update(Markdown(text_buffer))

            case EventType.TOOL_CALL_START:
                if live:
                    live.stop()
                    live = None
                    text_buffer = ""
                io.console.print(f"\n⏳ [bold yellow]{event.data['name']}[/]")

            case EventType.TOOL_CALL_RESULT:
                icon = "✅" if event.data.get("success") else "❌"
                io.console.print(f"{icon} {event.data.get('summary', '')}")

            case EventType.DONE:
                if live:
                    live.stop()
```

#### 3. Notification When Agent Finishes

```python
import shutil, subprocess, platform

def notify_user(message="Agent is waiting for input"):
    system = platform.system()
    if system == "Darwin" and shutil.which("terminal-notifier"):
        subprocess.run(["terminal-notifier", "-title", "Agent", "-message", message])
    elif system == "Linux" and shutil.which("notify-send"):
        subprocess.run(["notify-send", "Agent", message])
    else:
        print("\a", end="", flush=True)  # Terminal bell
```

#### 4. Multiline Input Support

```python
from prompt_toolkit.key_binding import KeyBindings

kb = KeyBindings()

@kb.add("enter")
def handle_enter(event):
    if multiline_mode:
        event.current_buffer.insert_text("\n")
    else:
        event.current_buffer.validate_and_handle()

@kb.add("escape", "enter")  # Alt+Enter submits in multiline
def handle_submit(event):
    event.current_buffer.validate_and_handle()
```

### What NOT to Do

1. **Don't use `input()`** — no history, no completion, no key bindings
2. **Don't block the event loop** during LLM calls — use async throughout
3. **Don't re-render full Markdown on every token** — throttle with `refresh_per_second`
4. **Don't build your own terminal UI framework** — prompt_toolkit + rich covers 99% of needs
5. **Don't go full-screen** unless you have a team — complexity is enormous (Claude Code's 251KB Ink class, OpenCode's 14,600+ line TUI)
6. **Don't skip `--yes` / `--no-confirm`** — essential for CI/scripting/batch usage

### Production Checklist

- [ ] `prompt_toolkit.PromptSession` with `FileHistory`
- [ ] Rich `Console` with `NO_COLOR` env var support
- [ ] Streaming via `rich.live.Live` + `rich.markdown.Markdown`
- [ ] Tool call rendering with status icons (⏳/✅/❌)
- [ ] Approval flow with Yes/No/All/Skip options
- [ ] `--yes` flag for non-interactive mode
- [ ] `--no-stream` flag for non-streaming mode
- [ ] Terminal bell / OS notification when agent needs input
- [ ] Graceful Ctrl+C handling (interrupt operation, not exit)
- [ ] Ctrl+D to exit cleanly
- [ ] Multiline input mode (toggle or Alt+Enter)
- [ ] `/command` system for meta-operations
- [ ] Chat history saved to markdown file

---

## Sources

- **Aider** (42k⭐) — `aider/io.py`, `aider/mdstream.py`: prompt_toolkit + Rich
- **gptme** (4.1k⭐) — Python agent CLI with tools, streaming, subagents
- **pi-tui** blog by Mario Zechner — Deep analysis of TUI approaches for coding agents
- **PydanticAI** `agent.to_cli_sync()` — Minimal REPL (Martin Fowler article)
- **TermTalk** — asyncio + prompt_toolkit + OpenAI streaming
- **Claude Code** architecture — Ink full-screen TUI (TypeScript)
- **OpenCode** architecture — Solid.js full-screen TUI (TypeScript)
- **Amp** — Full-screen Go/TypeScript TUI