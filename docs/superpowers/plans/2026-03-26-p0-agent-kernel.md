# P0: Agent Kernel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the minimal agent loop that can receive a goal, read files, execute commands, edit files via search-and-replace, and produce a result.

**Architecture:** A while-loop kernel that calls an LLM, dispatches tool calls, and feeds results back. Tape (append-only JSONL) stores all events. Context module assembles LLM-ready messages from tape entries. One provider (OpenAI-compatible) for P0.

**Tech Stack:** Python 3.12+, uv, asyncio, httpx, openai SDK, Pydantic v2, pytest, structlog

**Spec:** `docs/superpowers/specs/2026-03-26-python-coding-agent-design.md` (Sections 3-8, 12)

---

## File Map

```
coding-agent/                        # New project root (sibling to agent-coding-loop/)
  pyproject.toml                     # Project metadata, dependencies, CLI entry points
  src/coding_agent/
    __init__.py
    __main__.py                      # CLI entry: python -m coding_agent
    core/
      __init__.py
      config.py                      # Config model + load_config()
      tape.py                        # Tape + Entry (append-only JSONL)
      context.py                     # Context: build working set from tape
      wire.py                        # WireMessage + WireConsumer protocol
      loop.py                        # AgentLoop: the kernel
      doom.py                        # DoomDetector
    providers/
      __init__.py
      base.py                        # ChatProvider protocol + types
      openai_compat.py               # OpenAI-compatible provider
    tools/
      __init__.py
      registry.py                    # ToolRegistry
      file.py                        # file_read, file_write, file_replace
      shell.py                       # bash tool
      search.py                      # grep, glob
    ui/
      __init__.py
      headless.py                    # Headless WireConsumer for batch mode
  tests/
    __init__.py
    core/
      __init__.py
      test_tape.py
      test_context.py
      test_loop.py
      test_doom.py
      test_config.py
    providers/
      __init__.py
      test_openai_compat.py
    tools/
      __init__.py
      test_registry.py
      test_file.py
      test_shell.py
      test_search.py
```

---

## Task 1: Project Scaffolding

**Files:**
- Create: `coding-agent/pyproject.toml`
- Create: `coding-agent/src/coding_agent/__init__.py`
- Create: `coding-agent/src/coding_agent/__main__.py`
- Create: `coding-agent/tests/__init__.py`

- [ ] **Step 1: Create project directory and pyproject.toml**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop
mkdir -p coding-agent/src/coding_agent coding-agent/tests
```

`coding-agent/pyproject.toml`:

```toml
[project]
name = "coding-agent"
version = "0.1.0"
description = "Interactive coding agent with tape-based context"
requires-python = ">=3.12"
dependencies = [
    "openai>=1.50.0",
    "httpx>=0.27.0",
    "pydantic>=2.0.0",
    "structlog>=24.0.0",
    "click>=8.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
    "pytest-cov>=5.0.0",
]

[project.scripts]
coding-agent = "coding_agent.__main__:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/coding_agent"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Create package init and main entry point**

`coding-agent/src/coding_agent/__init__.py`:

```python
"""Interactive coding agent with tape-based context."""
```

`coding-agent/src/coding_agent/__main__.py`:

```python
"""CLI entry point: python -m coding_agent"""

import click


@click.group()
def main():
    """Coding Agent CLI."""
    pass


@main.command()
@click.option("--goal", required=True, help="Task goal for the agent")
@click.option("--repo", default=".", help="Repository path")
@click.option("--model", default="gpt-4o", help="Model name")
@click.option("--base-url", default=None, help="OpenAI-compatible API base URL")
@click.option("--api-key", envvar="AGENT_API_KEY", required=True, help="API key")
@click.option("--max-steps", default=30, help="Max steps per turn")
@click.option("--approval", default="yolo", type=click.Choice(["yolo", "interactive", "auto"]))
def run(goal, repo, model, base_url, api_key, max_steps, approval):
    """Run agent on a goal (batch mode)."""
    import asyncio
    from coding_agent.core.config import Config

    config = Config(
        model=model,
        api_key=api_key,
        base_url=base_url,
        repo=repo,
        max_steps=max_steps,
        approval_mode=approval,
    )
    asyncio.run(_run(config, goal))


async def _run(config, goal):
    from coding_agent.core.loop import AgentLoop
    from coding_agent.providers.openai_compat import OpenAICompatProvider
    from coding_agent.tools.registry import ToolRegistry
    from coding_agent.tools.file import register_file_tools
    from coding_agent.tools.shell import register_shell_tools
    from coding_agent.tools.search import register_search_tools
    from coding_agent.core.tape import Tape
    from coding_agent.core.context import Context
    from coding_agent.ui.headless import HeadlessConsumer  # noqa: WireConsumer impl

    tape = Tape.create(config.tape_dir)
    provider = OpenAICompatProvider(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
    )
    registry = ToolRegistry()
    register_file_tools(registry, repo_root=config.repo)
    register_shell_tools(registry, cwd=config.repo)
    register_search_tools(registry, repo_root=config.repo)

    system_prompt = (
        "You are a coding agent. You can read files, edit files, "
        "run shell commands, and search the codebase to accomplish tasks."
    )
    context = Context(provider.max_context_size, system_prompt)
    consumer = HeadlessConsumer()

    loop = AgentLoop(
        provider=provider,
        tools=registry,
        tape=tape,
        context=context,
        consumer=consumer,
        max_steps=config.max_steps,
    )

    result = await loop.run_turn(goal)
    click.echo(f"\n--- Result ({result.stop_reason}) ---")
    if result.final_message:
        click.echo(result.final_message)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Create test init files**

```bash
mkdir -p coding-agent/tests/core coding-agent/tests/providers coding-agent/tests/tools
touch coding-agent/tests/__init__.py
touch coding-agent/tests/core/__init__.py
touch coding-agent/tests/providers/__init__.py
touch coding-agent/tests/tools/__init__.py
```

- [ ] **Step 4: Install project in dev mode and verify**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv sync --all-extras
uv run python -m coding_agent --help
```

Expected: Shows CLI help with `run` command.

- [ ] **Step 5: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop
git add coding-agent/
git commit -m "feat(p0): scaffold Python coding-agent project"
```

---

## Task 2: Config Module

**Files:**
- Create: `coding-agent/src/coding_agent/core/__init__.py`
- Create: `coding-agent/src/coding_agent/core/config.py`
- Test: `coding-agent/tests/core/test_config.py`

- [ ] **Step 1: Write the failing tests**

`coding-agent/tests/core/test_config.py`:

```python
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from coding_agent.core.config import Config, load_config


class TestConfig:
    def test_defaults(self):
        c = Config(api_key="sk-test")
        assert c.model == "gpt-4o"
        assert c.provider == "openai"
        assert c.max_steps == 30
        assert c.doom_threshold == 3
        assert c.approval_mode == "yolo"

    def test_api_key_required(self):
        with pytest.raises(ValidationError):
            Config()

    def test_api_key_is_secret(self):
        c = Config(api_key="sk-secret")
        assert "sk-secret" not in repr(c)
        assert c.api_key.get_secret_value() == "sk-secret"

    def test_custom_values(self):
        c = Config(
            api_key="sk-test",
            model="claude-sonnet-4-20250514",
            provider="anthropic",
            base_url="https://api.example.com/v1",
            max_steps=10,
            doom_threshold=5,
            repo=Path("/tmp/test-repo"),
        )
        assert c.model == "claude-sonnet-4-20250514"
        assert c.provider == "anthropic"
        assert c.base_url == "https://api.example.com/v1"
        assert c.max_steps == 10
        assert c.repo == Path("/tmp/test-repo")


class TestLoadConfig:
    def test_env_vars_override_defaults(self, monkeypatch):
        monkeypatch.setenv("AGENT_API_KEY", "sk-from-env")
        monkeypatch.setenv("AGENT_MODEL", "gpt-4o-mini")
        c = load_config()
        assert c.api_key.get_secret_value() == "sk-from-env"
        assert c.model == "gpt-4o-mini"

    def test_cli_args_override_env(self, monkeypatch):
        monkeypatch.setenv("AGENT_API_KEY", "sk-from-env")
        monkeypatch.setenv("AGENT_MODEL", "gpt-4o-mini")
        c = load_config(cli_args={"model": "gpt-4o", "api_key": "sk-cli"})
        assert c.model == "gpt-4o"
        assert c.api_key.get_secret_value() == "sk-cli"

    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("AGENT_API_KEY", raising=False)
        with pytest.raises(ValidationError):
            load_config()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_config.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'coding_agent.core.config'`

- [ ] **Step 3: Write minimal implementation**

`coding-agent/src/coding_agent/core/__init__.py`:

```python
```

`coding-agent/src/coding_agent/core/config.py`:

```python
"""Configuration with layered precedence: CLI flags > env vars > defaults."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, SecretStr


class Config(BaseModel):
    """Validated agent configuration."""

    # Provider
    provider: Literal["openai", "anthropic"] = "openai"
    model: str = "gpt-4o"
    api_key: SecretStr
    base_url: str | None = None

    # Agent behavior
    max_steps: int = 30
    approval_mode: Literal["yolo", "interactive", "auto"] = "yolo"
    doom_threshold: int = 3

    # Paths
    repo: Path = Path(".")
    tape_dir: Path = Path.home() / ".coding-agent" / "tapes"
    skills_dir: Path = Path.home() / ".coding-agent" / "skills"

    # Sub-agents
    max_subagent_depth: int = 3
    subagent_max_steps: int = 15


# Env var prefix → Config field mapping
_ENV_MAP: dict[str, str] = {
    "AGENT_API_KEY": "api_key",
    "AGENT_MODEL": "model",
    "AGENT_BASE_URL": "base_url",
    "AGENT_PROVIDER": "provider",
    "AGENT_MAX_STEPS": "max_steps",
    "AGENT_APPROVAL_MODE": "approval_mode",
    "AGENT_DOOM_THRESHOLD": "doom_threshold",
    "AGENT_REPO": "repo",
}


def load_config(cli_args: dict | None = None) -> Config:
    """Load config with precedence: CLI flags > env vars > defaults."""
    values: dict = {}

    # Layer 1: env vars
    for env_key, field_name in _ENV_MAP.items():
        val = os.environ.get(env_key)
        if val is not None:
            values[field_name] = val

    # Layer 2: CLI args override env
    if cli_args:
        for k, v in cli_args.items():
            if v is not None:
                values[k] = v

    return Config(**values)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_config.py -v
```

Expected: All 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add coding-agent/src/coding_agent/core/ coding-agent/tests/core/test_config.py
git commit -m "feat(p0): add Config model with env var + CLI layering"
```

---

## Task 3: Tape (Append-Only JSONL Storage)

**Files:**
- Create: `coding-agent/src/coding_agent/core/tape.py`
- Test: `coding-agent/tests/core/test_tape.py`

- [ ] **Step 1: Write the failing tests**

`coding-agent/tests/core/test_tape.py`:

```python
import json
from pathlib import Path

import pytest

from coding_agent.core.tape import Entry, Tape


class TestEntry:
    def test_message_entry(self):
        e = Entry.message("user", "hello")
        assert e.kind == "message"
        assert e.payload == {"role": "user", "content": "hello"}
        assert e.id == 0  # unassigned until appended

    def test_anchor_entry(self):
        e = Entry.anchor("phase1", {"summary": "done"})
        assert e.kind == "anchor"
        assert e.payload == {"name": "phase1", "state": {"summary": "done"}}

    def test_tool_call_entry(self):
        e = Entry.tool_call("call_1", "bash", {"cmd": "ls"})
        assert e.kind == "tool_call"
        assert e.payload["call_id"] == "call_1"
        assert e.payload["tool"] == "bash"
        assert e.payload["args"] == {"cmd": "ls"}

    def test_tool_result_entry(self):
        e = Entry.tool_result("call_1", "file1.py\nfile2.py")
        assert e.kind == "tool_result"
        assert e.payload["call_id"] == "call_1"
        assert e.payload["result"] == "file1.py\nfile2.py"

    def test_entry_is_frozen(self):
        e = Entry.message("user", "hello")
        with pytest.raises(AttributeError):
            e.kind = "anchor"


class TestTape:
    def test_append_and_read(self, tmp_path):
        tape = Tape(tmp_path / "test.jsonl")
        tape.append(Entry.message("user", "hello"))
        tape.append(Entry.message("assistant", "hi"))
        entries = tape.entries()
        assert len(entries) == 2
        assert entries[0].id == 1
        assert entries[1].id == 2
        assert entries[0].payload["content"] == "hello"

    def test_persistence(self, tmp_path):
        path = tmp_path / "test.jsonl"
        tape1 = Tape(path)
        tape1.append(Entry.message("user", "hello"))
        tape1.append(Entry.message("assistant", "hi"))

        # Reload from disk
        tape2 = Tape(path)
        entries = tape2.entries()
        assert len(entries) == 2
        assert entries[0].payload["content"] == "hello"

    def test_jsonl_format(self, tmp_path):
        path = tmp_path / "test.jsonl"
        tape = Tape(path)
        tape.append(Entry.message("user", "hello"))
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["kind"] == "message"
        assert data["id"] == 1

    def test_entries_after_anchor(self, tmp_path):
        tape = Tape(tmp_path / "test.jsonl")
        tape.append(Entry.message("user", "old message"))
        tape.handoff("checkpoint", {"summary": "phase 1 done"})
        tape.append(Entry.message("user", "new message"))

        all_entries = tape.entries()
        assert len(all_entries) == 3

        after = tape.entries(after_anchor="checkpoint")
        assert len(after) == 2  # anchor + new message
        assert after[0].kind == "anchor"
        assert after[1].payload["content"] == "new message"

    def test_handoff_creates_anchor(self, tmp_path):
        tape = Tape(tmp_path / "test.jsonl")
        tape.handoff("phase1", {"key": "value"})
        entries = tape.entries()
        assert len(entries) == 1
        assert entries[0].kind == "anchor"
        assert entries[0].payload["name"] == "phase1"
        assert entries[0].payload["state"] == {"key": "value"}


class TestTapeFork:
    def test_fork_creates_independent_copy(self, tmp_path):
        tape = Tape(tmp_path / "test.jsonl")
        tape.append(Entry.message("user", "hello"))

        forked = tape.fork()
        forked.append(Entry.message("assistant", "from fork"))

        # Original tape unaffected
        assert len(tape.entries()) == 1
        # Forked tape has both
        assert len(forked.entries()) == 2

    def test_fork_is_in_memory(self, tmp_path):
        tape = Tape(tmp_path / "test.jsonl")
        tape.append(Entry.message("user", "hello"))

        forked = tape.fork()
        forked.append(Entry.message("assistant", "from fork"))

        # No new file created
        assert not (tmp_path / "test_fork.jsonl").exists()

    def test_merge_appends_new_entries(self, tmp_path):
        tape = Tape(tmp_path / "test.jsonl")
        tape.append(Entry.message("user", "hello"))

        forked = tape.fork()
        forked.append(Entry.message("assistant", "from fork"))
        forked.append(Entry.tool_call("c1", "bash", {"cmd": "ls"}))

        tape.merge(forked)
        entries = tape.entries()
        assert len(entries) == 3  # original + 2 merged
        assert entries[1].payload["content"] == "from fork"
        assert entries[2].kind == "tool_call"

    def test_merge_persists_to_disk(self, tmp_path):
        path = tmp_path / "test.jsonl"
        tape = Tape(path)
        tape.append(Entry.message("user", "hello"))

        forked = tape.fork()
        forked.append(Entry.message("assistant", "merged"))
        tape.merge(forked)

        # Reload and verify
        tape2 = Tape(path)
        assert len(tape2.entries()) == 2

    @staticmethod
    def _create_tape(tmp_path):
        return Tape(tmp_path / "test.jsonl")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_tape.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

`coding-agent/src/coding_agent/core/tape.py`:

```python
"""Tape: append-only fact storage as JSONL."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

EntryKind = Literal["message", "tool_call", "tool_result", "anchor", "event"]


@dataclass(frozen=True, slots=True)
class Entry:
    id: int
    kind: EntryKind
    payload: dict[str, Any]
    timestamp: str

    @classmethod
    def message(cls, role: str, content: str) -> Entry:
        return cls(
            id=0,
            kind="message",
            payload={"role": role, "content": content},
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def anchor(cls, name: str, state: dict[str, Any]) -> Entry:
        return cls(
            id=0,
            kind="anchor",
            payload={"name": name, "state": state},
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def tool_call(cls, call_id: str, tool: str, args: dict[str, Any]) -> Entry:
        # TODO(P1): refactor to accept ToolCall dataclass per spec section 3.1
        return cls(
            id=0,
            kind="tool_call",
            payload={"call_id": call_id, "tool": tool, "args": args},
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def tool_result(cls, call_id: str, result: str) -> Entry:
        return cls(
            id=0,
            kind="tool_result",
            payload={"call_id": call_id, "result": result},
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def event(cls, name: str, data: dict[str, Any] | None = None) -> Entry:
        return cls(
            id=0,
            kind="event",
            payload={"name": name, "data": data or {}},
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Entry:
        return cls(
            id=data["id"],
            kind=data["kind"],
            payload=data["payload"],
            timestamp=data["timestamp"],
        )


class Tape:
    """Append-only sequence of entries, persisted as JSONL.

    If path is None, operates in memory only (for forked tapes).
    """

    def __init__(self, path: Path | None):
        self._path = path
        self._entries: list[Entry] = []
        self._next_id = 1
        if path is not None and path.exists():
            self._load()

    @classmethod
    def create(cls, tape_dir: Path) -> Tape:
        """Create a new tape with a unique filename in tape_dir."""
        import uuid

        tape_dir.mkdir(parents=True, exist_ok=True)
        path = tape_dir / f"{uuid.uuid4()}.jsonl"
        return cls(path)

    def _load(self) -> None:
        for line in self._path.read_text().strip().split("\n"):
            if line:
                data = json.loads(line)
                entry = Entry.from_dict(data)
                self._entries.append(entry)
                if entry.id >= self._next_id:
                    self._next_id = entry.id + 1

    def append(self, entry: Entry) -> Entry:
        """Append an entry, assigning it a sequential ID."""
        assigned = Entry(
            id=self._next_id,
            kind=entry.kind,
            payload=entry.payload,
            timestamp=entry.timestamp,
        )
        self._entries.append(assigned)
        self._next_id += 1

        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a") as f:
                f.write(json.dumps(assigned.to_dict(), ensure_ascii=False) + "\n")

        return assigned

    def entries(self, after_anchor: str | None = None) -> list[Entry]:
        """Return entries, optionally starting from a named anchor."""
        if after_anchor is None:
            return list(self._entries)

        # Find the last anchor with this name
        anchor_idx = None
        for i, e in enumerate(self._entries):
            if e.kind == "anchor" and e.payload.get("name") == after_anchor:
                anchor_idx = i

        if anchor_idx is None:
            return list(self._entries)

        return list(self._entries[anchor_idx:])

    def handoff(self, name: str, state: dict[str, Any]) -> None:
        """Create an anchor entry marking a phase transition."""
        self.append(Entry.anchor(name, state))

    def fork(self) -> Tape:
        """Create an in-memory fork for sub-agent execution."""
        forked = Tape(path=None)
        forked._entries = list(self._entries)
        forked._next_id = self._next_id
        return forked

    def merge(self, forked: Tape) -> None:
        """Merge new entries from a forked tape back into this tape."""
        fork_point = len(self._entries)
        new_entries = forked._entries[fork_point:]
        for entry in new_entries:
            self.append(entry)

    def __len__(self) -> int:
        return len(self._entries)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_tape.py -v
```

Expected: All 12 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add coding-agent/src/coding_agent/core/tape.py coding-agent/tests/core/test_tape.py
git commit -m "feat(p0): add Tape with append-only JSONL, fork/merge, anchors"
```

---

## Task 4: Context (Working Set Assembly)

**Files:**
- Create: `coding-agent/src/coding_agent/core/context.py`
- Test: `coding-agent/tests/core/test_context.py`

- [ ] **Step 1: Write the failing tests**

`coding-agent/tests/core/test_context.py`:

```python
from coding_agent.core.context import Context
from coding_agent.core.tape import Entry, Tape


SYSTEM_PROMPT = "You are a coding agent."


class TestContext:
    def _make_tape(self, tmp_path):
        return Tape(tmp_path / "test.jsonl")

    def test_basic_working_set(self, tmp_path):
        tape = self._make_tape(tmp_path)
        tape.append(Entry.message("user", "Fix the bug"))
        tape.append(Entry.message("assistant", "I'll look at the code"))

        ctx = Context(max_tokens=100_000, system_prompt=SYSTEM_PROMPT)
        messages = ctx.build_working_set(tape)

        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == SYSTEM_PROMPT
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Fix the bug"
        assert messages[2]["role"] == "assistant"
        assert messages[2]["content"] == "I'll look at the code"

    def test_tool_calls_in_working_set(self, tmp_path):
        tape = self._make_tape(tmp_path)
        tape.append(Entry.message("user", "List files"))
        tape.append(Entry.tool_call("c1", "bash", {"cmd": "ls"}))
        tape.append(Entry.tool_result("c1", "file1.py\nfile2.py"))

        ctx = Context(max_tokens=100_000, system_prompt=SYSTEM_PROMPT)
        messages = ctx.build_working_set(tape)

        # system + user + assistant(tool_call) + tool_result
        assert len(messages) == 4
        assert messages[2]["role"] == "assistant"
        assert messages[2]["tool_calls"][0]["id"] == "c1"
        assert messages[3]["role"] == "tool"
        assert messages[3]["tool_call_id"] == "c1"

    def test_anchor_truncation(self, tmp_path):
        tape = self._make_tape(tmp_path)
        tape.append(Entry.message("user", "old task"))
        tape.append(Entry.message("assistant", "old response"))
        tape.handoff("checkpoint", {"summary": "Phase 1 done"})
        tape.append(Entry.message("user", "new task"))

        ctx = Context(max_tokens=100_000, system_prompt=SYSTEM_PROMPT)
        messages = ctx.build_working_set(tape)

        # system + anchor_as_system + user("new task")
        # Old messages before anchor should be excluded
        contents = [m.get("content", "") for m in messages]
        assert "old task" not in contents
        assert "new task" in contents

    def test_event_entries_excluded(self, tmp_path):
        tape = self._make_tape(tmp_path)
        tape.append(Entry.message("user", "hello"))
        tape.append(Entry.event("loop.step", {"step": 1}))
        tape.append(Entry.message("assistant", "hi"))

        ctx = Context(max_tokens=100_000, system_prompt=SYSTEM_PROMPT)
        messages = ctx.build_working_set(tape)

        # system + user + assistant (event excluded)
        assert len(messages) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_context.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

`coding-agent/src/coding_agent/core/context.py`:

```python
"""Context: assemble LLM-ready messages from tape entries."""

from __future__ import annotations

from typing import Any

from coding_agent.core.tape import Entry, Tape


class Context:
    """Builds a working set of messages from tape entries.

    Strategy for P0 (basic):
    1. Find the most recent anchor → start from there
    2. Convert entries to OpenAI-format messages
    3. Exclude event entries (not useful for LLM reasoning)
    4. Prepend system prompt
    """

    def __init__(self, max_tokens: int, system_prompt: str):
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt

    def build_working_set(self, tape: Tape) -> list[dict[str, Any]]:
        """Assemble LLM-ready messages from tape entries."""
        messages: list[dict[str, Any]] = []

        # System prompt always first
        messages.append({"role": "system", "content": self.system_prompt})

        # Find the last anchor to start from
        entries = tape.entries()
        start_idx = 0
        for i in range(len(entries) - 1, -1, -1):
            if entries[i].kind == "anchor":
                start_idx = i
                break

        # Convert entries to messages
        for entry in entries[start_idx:]:
            msg = self._entry_to_message(entry)
            if msg is not None:
                messages.append(msg)

        return messages

    def _entry_to_message(self, entry: Entry) -> dict[str, Any] | None:
        match entry.kind:
            case "message":
                return {
                    "role": entry.payload["role"],
                    "content": entry.payload["content"],
                }
            case "anchor":
                state = entry.payload.get("state", {})
                name = entry.payload.get("name", "checkpoint")
                summary = state.get("summary", f"Phase: {name}")
                return {
                    "role": "system",
                    "content": f"[Checkpoint: {name}] {summary}",
                }
            case "tool_call":
                return {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": entry.payload["call_id"],
                            "type": "function",
                            "function": {
                                "name": entry.payload["tool"],
                                "arguments": __import__("json").dumps(
                                    entry.payload["args"]
                                ),
                            },
                        }
                    ],
                }
            case "tool_result":
                return {
                    "role": "tool",
                    "tool_call_id": entry.payload["call_id"],
                    "content": entry.payload["result"],
                }
            case "event":
                return None  # Events excluded from LLM context
            case _:
                return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_context.py -v
```

Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add coding-agent/src/coding_agent/core/context.py coding-agent/tests/core/test_context.py
git commit -m "feat(p0): add Context with anchor-based working set assembly"
```

---

## Task 5: Doom Loop Detector

**Files:**
- Create: `coding-agent/src/coding_agent/core/doom.py`
- Test: `coding-agent/tests/core/test_doom.py`

- [ ] **Step 1: Write the failing tests**

`coding-agent/tests/core/test_doom.py`:

```python
from coding_agent.core.doom import DoomDetector


class TestDoomDetector:
    def test_no_doom_on_different_calls(self):
        d = DoomDetector(threshold=3)
        assert not d.observe("bash", {"cmd": "ls"})
        assert not d.observe("bash", {"cmd": "cat foo"})
        assert not d.observe("file_read", {"path": "bar.py"})

    def test_doom_on_repeated_calls(self):
        d = DoomDetector(threshold=3)
        assert not d.observe("bash", {"cmd": "ls"})
        assert not d.observe("bash", {"cmd": "ls"})
        assert d.observe("bash", {"cmd": "ls"})  # 3rd time

    def test_reset_on_different_input(self):
        d = DoomDetector(threshold=3)
        assert not d.observe("bash", {"cmd": "ls"})
        assert not d.observe("bash", {"cmd": "ls"})
        assert not d.observe("bash", {"cmd": "cat foo"})  # different → reset
        assert not d.observe("bash", {"cmd": "cat foo"})
        assert d.observe("bash", {"cmd": "cat foo"})  # 3rd time

    def test_custom_threshold(self):
        d = DoomDetector(threshold=2)
        assert not d.observe("bash", {"cmd": "ls"})
        assert d.observe("bash", {"cmd": "ls"})  # 2nd time

    def test_different_tool_same_args_resets(self):
        d = DoomDetector(threshold=3)
        assert not d.observe("bash", {"cmd": "ls"})
        assert not d.observe("bash", {"cmd": "ls"})
        assert not d.observe("file_read", {"cmd": "ls"})  # different tool
        assert not d.observe("file_read", {"cmd": "ls"})

    def test_count_property(self):
        d = DoomDetector(threshold=5)
        d.observe("bash", {"cmd": "ls"})
        assert d.count == 1
        d.observe("bash", {"cmd": "ls"})
        assert d.count == 2
        d.observe("bash", {"cmd": "cat"})  # reset
        assert d.count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_doom.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

`coding-agent/src/coding_agent/core/doom.py`:

```python
"""Doom loop detection: abort when the agent repeats identical tool calls."""

from __future__ import annotations

import hashlib
import json


class DoomDetector:
    """Detect repetitive tool calls that indicate the agent is stuck."""

    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self._last_tool: str | None = None
        self._last_hash: str | None = None
        self._count: int = 0

    @property
    def count(self) -> int:
        return self._count

    def observe(self, tool: str, args: dict) -> bool:
        """Record a tool call. Returns True if doom loop detected."""
        args_hash = hashlib.md5(
            json.dumps(args, sort_keys=True).encode()
        ).hexdigest()

        if tool == self._last_tool and args_hash == self._last_hash:
            self._count += 1
        else:
            self._last_tool = tool
            self._last_hash = args_hash
            self._count = 1

        return self._count >= self.threshold

    def reset(self) -> None:
        self._last_tool = None
        self._last_hash = None
        self._count = 0
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_doom.py -v
```

Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add coding-agent/src/coding_agent/core/doom.py coding-agent/tests/core/test_doom.py
git commit -m "feat(p0): add DoomDetector for repetitive tool call detection"
```

---

## Task 6: ChatProvider Protocol + OpenAI-Compatible Provider

**Files:**
- Create: `coding-agent/src/coding_agent/providers/__init__.py`
- Create: `coding-agent/src/coding_agent/providers/base.py`
- Create: `coding-agent/src/coding_agent/providers/openai_compat.py`
- Test: `coding-agent/tests/providers/test_openai_compat.py`

- [ ] **Step 1: Write the failing tests**

`coding-agent/tests/providers/test_openai_compat.py`:

```python
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from coding_agent.providers.base import StreamEvent, ToolCallEvent, ToolSchema
from coding_agent.providers.openai_compat import OpenAICompatProvider


class TestOpenAICompatProvider:
    def test_init(self):
        p = OpenAICompatProvider(model="gpt-4o", api_key="sk-test")
        assert p.model_name == "gpt-4o"
        assert p.max_context_size == 128_000

    def test_custom_base_url(self):
        p = OpenAICompatProvider(
            model="gpt-4o", api_key="sk-test", base_url="https://custom.api/v1"
        )
        assert p.model_name == "gpt-4o"

    def test_tool_schema_format(self):
        schema = ToolSchema(
            name="bash",
            description="Run a shell command",
            parameters={
                "type": "object",
                "properties": {"cmd": {"type": "string"}},
                "required": ["cmd"],
            },
        )
        openai_format = schema.to_openai()
        assert openai_format["type"] == "function"
        assert openai_format["function"]["name"] == "bash"

    @pytest.mark.asyncio
    async def test_stream_text_response(self):
        """Test that a text response (no tool calls) streams correctly."""
        p = OpenAICompatProvider(model="gpt-4o", api_key="sk-test")

        # Mock the OpenAI client stream
        mock_chunk_1 = MagicMock()
        mock_chunk_1.choices = [MagicMock()]
        mock_chunk_1.choices[0].delta.content = "Hello"
        mock_chunk_1.choices[0].delta.tool_calls = None
        mock_chunk_1.choices[0].finish_reason = None

        mock_chunk_2 = MagicMock()
        mock_chunk_2.choices = [MagicMock()]
        mock_chunk_2.choices[0].delta.content = " world"
        mock_chunk_2.choices[0].delta.tool_calls = None
        mock_chunk_2.choices[0].finish_reason = "stop"

        async def mock_chunks():
            yield mock_chunk_1
            yield mock_chunk_2

        # create() returns an awaitable async-iterable; mock it accordingly
        mock_response = mock_chunks()
        create_coro = AsyncMock(return_value=mock_response)

        with patch.object(p._client.chat.completions, "create", create_coro):
            events = []
            async for event in p.stream(
                messages=[{"role": "user", "content": "hi"}]
            ):
                events.append(event)

        deltas = [e for e in events if e.type == "delta"]
        assert len(deltas) == 2
        assert deltas[0].text == "Hello"
        assert deltas[1].text == " world"
        assert events[-1].type == "done"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/providers/test_openai_compat.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the protocol and types**

`coding-agent/src/coding_agent/providers/__init__.py`:

```python
```

`coding-agent/src/coding_agent/providers/base.py`:

```python
"""ChatProvider protocol and shared types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ToolSchema:
    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True, slots=True)
class ToolCallEvent:
    call_id: str
    tool: str
    args: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StreamEvent:
    type: Literal["delta", "tool_call", "done", "error"]
    text: str | None = None
    tool_calls: list[ToolCallEvent] = field(default_factory=list)
    error: str | None = None


@runtime_checkable
class ChatProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def max_context_size(self) -> int: ...

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
    ) -> AsyncIterator[StreamEvent]: ...
```

- [ ] **Step 4: Write the OpenAI-compatible provider**

`coding-agent/src/coding_agent/providers/openai_compat.py`:

```python
"""OpenAI-compatible provider (GPT, Deepseek, Qwen, proxies)."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from coding_agent.providers.base import StreamEvent, ToolCallEvent, ToolSchema

# Model name → context window size (common models)
_CONTEXT_SIZES: dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
}
_DEFAULT_CONTEXT_SIZE = 128_000


class OpenAICompatProvider:
    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
    ):
        self._model = model
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._max_context = _CONTEXT_SIZES.get(model, _DEFAULT_CONTEXT_SIZE)

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def max_context_size(self) -> int:
        return self._max_context

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = [t.to_openai() for t in tools]

        # Accumulate tool call chunks
        tool_call_buffers: dict[int, dict[str, Any]] = defaultdict(
            lambda: {"id": "", "name": "", "args": ""}
        )

        response = await self._client.chat.completions.create(**kwargs)

        async for chunk in response:
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

            # Text content
            if delta.content:
                yield StreamEvent(type="delta", text=delta.content)

            # Tool calls (streamed incrementally)
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    buf = tool_call_buffers[tc.index]
                    if tc.id:
                        buf["id"] = tc.id
                    if tc.function and tc.function.name:
                        buf["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        buf["args"] += tc.function.arguments

            # Turn complete
            if chunk.choices[0].finish_reason is not None:
                if tool_call_buffers:
                    calls = []
                    for buf in tool_call_buffers.values():
                        try:
                            args = json.loads(buf["args"]) if buf["args"] else {}
                        except json.JSONDecodeError:
                            args = {}
                        calls.append(
                            ToolCallEvent(
                                call_id=buf["id"],
                                tool=buf["name"],
                                args=args,
                            )
                        )
                    yield StreamEvent(type="tool_call", tool_calls=calls)

                yield StreamEvent(type="done")
                return
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/providers/test_openai_compat.py -v
```

Expected: All 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add coding-agent/src/coding_agent/providers/ coding-agent/tests/providers/
git commit -m "feat(p0): add ChatProvider protocol + OpenAI-compatible provider"
```

---

## Task 7: Tool Registry

**Files:**
- Create: `coding-agent/src/coding_agent/tools/__init__.py`
- Create: `coding-agent/src/coding_agent/tools/registry.py`
- Test: `coding-agent/tests/tools/test_registry.py`

- [ ] **Step 1: Write the failing tests**

`coding-agent/tests/tools/test_registry.py`:

```python
import pytest

from coding_agent.tools.registry import ToolDef, ToolRegistry


async def echo_tool(text: str) -> str:
    return f"echo: {text}"


async def add_tool(a: int, b: int) -> str:
    return str(a + b)


class TestToolRegistry:
    def test_register_and_list(self):
        reg = ToolRegistry()
        reg.register(ToolDef(
            name="echo",
            description="Echo text",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            handler=echo_tool,
        ))
        assert len(reg.schemas()) == 1
        assert reg.schemas()[0].name == "echo"

    @pytest.mark.asyncio
    async def test_execute(self):
        reg = ToolRegistry()
        reg.register(ToolDef(
            name="echo",
            description="Echo text",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}},
            handler=echo_tool,
        ))
        result = await reg.execute("echo", {"text": "hello"})
        assert result == "echo: hello"

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        reg = ToolRegistry()
        result = await reg.execute("nonexistent", {})
        assert "unknown tool" in result.lower()

    @pytest.mark.asyncio
    async def test_execute_error_handling(self):
        async def failing_tool() -> str:
            raise ValueError("something broke")

        reg = ToolRegistry()
        reg.register(ToolDef(
            name="fail",
            description="Always fails",
            parameters={"type": "object", "properties": {}},
            handler=failing_tool,
        ))
        result = await reg.execute("fail", {})
        assert "error" in result.lower()
        assert "something broke" in result

    def test_multiple_tools(self):
        reg = ToolRegistry()
        reg.register(ToolDef(name="echo", description="Echo", parameters={}, handler=echo_tool))
        reg.register(ToolDef(name="add", description="Add", parameters={}, handler=add_tool))
        assert len(reg.schemas()) == 2
        names = {s.name for s in reg.schemas()}
        assert names == {"echo", "add"}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/tools/test_registry.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

`coding-agent/src/coding_agent/tools/__init__.py`:

```python
```

`coding-agent/src/coding_agent/tools/registry.py`:

```python
"""Tool registry: register, route, and execute tool calls."""

from __future__ import annotations

import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

from coding_agent.providers.base import ToolSchema

logger = structlog.get_logger()


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Awaitable[str]]


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool: ToolDef) -> None:
        self._tools[tool.name] = tool

    def schemas(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name=t.name,
                description=t.description,
                parameters=t.parameters,
            )
            for t in self._tools.values()
        ]

    async def execute(self, name: str, args: dict[str, Any]) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: Unknown tool '{name}'. Available: {', '.join(self._tools.keys())}"

        try:
            result = await tool.handler(**args)
            return result
        except Exception as e:
            logger.error("tool_execution_error", tool=name, error=str(e))
            return f"Error executing {name}: {type(e).__name__}: {e}"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/tools/test_registry.py -v
```

Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add coding-agent/src/coding_agent/tools/registry.py coding-agent/tests/tools/test_registry.py coding-agent/src/coding_agent/tools/__init__.py
git commit -m "feat(p0): add ToolRegistry with register, schema, execute"
```

---

## Task 8: File Tools (read, write, replace)

**Files:**
- Create: `coding-agent/src/coding_agent/tools/file.py`
- Test: `coding-agent/tests/tools/test_file.py`

- [ ] **Step 1: Write the failing tests**

`coding-agent/tests/tools/test_file.py`:

```python
from pathlib import Path

import pytest

from coding_agent.tools.file import file_read, file_write, file_replace, register_file_tools
from coding_agent.tools.registry import ToolRegistry


class TestFileRead:
    @pytest.mark.asyncio
    async def test_read_existing_file(self, tmp_path):
        (tmp_path / "test.py").write_text("print('hello')")
        result = await file_read(path="test.py", repo_root=tmp_path)
        assert "print('hello')" in result

    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self, tmp_path):
        result = await file_read(path="missing.py", repo_root=tmp_path)
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_read_rejects_path_traversal(self, tmp_path):
        result = await file_read(path="../../../etc/passwd", repo_root=tmp_path)
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_read_empty_path(self, tmp_path):
        result = await file_read(path="", repo_root=tmp_path)
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_read_max_bytes(self, tmp_path):
        (tmp_path / "big.txt").write_text("x" * 100_000)
        result = await file_read(path="big.txt", repo_root=tmp_path, max_bytes=1000)
        assert len(result) <= 1100  # 1000 + truncation message


class TestFileWrite:
    @pytest.mark.asyncio
    async def test_write_new_file(self, tmp_path):
        result = await file_write(path="new.py", content="print('new')", repo_root=tmp_path)
        assert "created" in result.lower()
        assert (tmp_path / "new.py").read_text() == "print('new')"

    @pytest.mark.asyncio
    async def test_write_creates_parent_dirs(self, tmp_path):
        result = await file_write(path="sub/dir/new.py", content="code", repo_root=tmp_path)
        assert "created" in result.lower()
        assert (tmp_path / "sub/dir/new.py").read_text() == "code"

    @pytest.mark.asyncio
    async def test_write_rejects_path_traversal(self, tmp_path):
        result = await file_write(path="../escape.py", content="bad", repo_root=tmp_path)
        assert "error" in result.lower()


class TestFileReplace:
    @pytest.mark.asyncio
    async def test_replace_exact_match(self, tmp_path):
        (tmp_path / "test.py").write_text("def foo():\n    return 1\n")
        result = await file_replace(
            path="test.py",
            old_string="return 1",
            new_string="return 2",
            repo_root=tmp_path,
        )
        assert "replaced" in result.lower()
        assert "return 2" in (tmp_path / "test.py").read_text()

    @pytest.mark.asyncio
    async def test_replace_no_match(self, tmp_path):
        (tmp_path / "test.py").write_text("def foo():\n    return 1\n")
        result = await file_replace(
            path="test.py",
            old_string="return 999",
            new_string="return 2",
            repo_root=tmp_path,
        )
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_replace_ambiguous_match(self, tmp_path):
        (tmp_path / "test.py").write_text("x = 1\ny = 1\nz = 1\n")
        result = await file_replace(
            path="test.py",
            old_string="1",
            new_string="2",
            repo_root=tmp_path,
        )
        assert "ambiguous" in result.lower() or "multiple" in result.lower()

    @pytest.mark.asyncio
    async def test_replace_nonexistent_file(self, tmp_path):
        result = await file_replace(
            path="missing.py",
            old_string="a",
            new_string="b",
            repo_root=tmp_path,
        )
        assert "error" in result.lower()


class TestRegisterFileTools:
    def test_registers_three_tools(self, tmp_path):
        reg = ToolRegistry()
        register_file_tools(reg, repo_root=tmp_path)
        names = {s.name for s in reg.schemas()}
        assert names == {"file_read", "file_write", "file_replace"}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/tools/test_file.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

`coding-agent/src/coding_agent/tools/file.py`:

```python
"""File tools: read, write, replace (search-and-replace)."""

from __future__ import annotations

from functools import partial
from pathlib import Path

from coding_agent.tools.registry import ToolDef, ToolRegistry

_DEFAULT_MAX_BYTES = 65_536


def _validate_path(path_str: str, repo_root: Path) -> Path | str:
    """Validate and resolve a path. Returns Path or error string."""
    if not path_str or not path_str.strip():
        return "Error: path is required; provide a relative file path"

    resolved = (repo_root / path_str).resolve()
    repo_resolved = repo_root.resolve()

    if not str(resolved).startswith(str(repo_resolved)):
        return f"Error: path '{path_str}' escapes repository root"

    return resolved


async def file_read(
    path: str, *, repo_root: Path, max_bytes: int = _DEFAULT_MAX_BYTES
) -> str:
    result = _validate_path(path, repo_root)
    if isinstance(result, str):
        return result

    if not result.exists():
        return f"Error: file '{path}' not found"
    if not result.is_file():
        return f"Error: '{path}' is not a file"

    try:
        content = result.read_text(errors="replace")
        if len(content) > max_bytes:
            content = content[:max_bytes] + f"\n... (truncated at {max_bytes} bytes)"
        return content
    except Exception as e:
        return f"Error reading '{path}': {e}"


async def file_write(path: str, content: str, *, repo_root: Path) -> str:
    result = _validate_path(path, repo_root)
    if isinstance(result, str):
        return result

    try:
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text(content)
        return f"Created {path} ({len(content)} bytes)"
    except Exception as e:
        return f"Error writing '{path}': {e}"


async def file_replace(
    path: str, old_string: str, new_string: str, *, repo_root: Path
) -> str:
    result = _validate_path(path, repo_root)
    if isinstance(result, str):
        return result

    if not result.exists():
        return f"Error: file '{path}' not found"

    try:
        content = result.read_text()
    except Exception as e:
        return f"Error reading '{path}': {e}"

    count = content.count(old_string)
    if count == 0:
        return f"Error: old_string not found in '{path}'"
    if count > 1:
        return f"Error: old_string matches {count} locations in '{path}' (ambiguous). Provide more context to make the match unique."

    new_content = content.replace(old_string, new_string, 1)
    result.write_text(new_content)
    return f"Replaced 1 occurrence in {path}"


def register_file_tools(registry: ToolRegistry, repo_root: Path) -> None:
    registry.register(ToolDef(
        name="file_read",
        description="Read file content from the repository",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path"},
            },
            "required": ["path"],
        },
        handler=partial(file_read, repo_root=repo_root),
    ))

    registry.register(ToolDef(
        name="file_write",
        description="Create a new file in the repository",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path"},
                "content": {"type": "string", "description": "File content"},
            },
            "required": ["path", "content"],
        },
        handler=partial(file_write, repo_root=repo_root),
    ))

    registry.register(ToolDef(
        name="file_replace",
        description="Replace text in an existing file (search-and-replace). old_string must match exactly once.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path"},
                "old_string": {"type": "string", "description": "Exact text to find"},
                "new_string": {"type": "string", "description": "Replacement text"},
            },
            "required": ["path", "old_string", "new_string"],
        },
        handler=partial(file_replace, repo_root=repo_root),
    ))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/tools/test_file.py -v
```

Expected: All 12 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add coding-agent/src/coding_agent/tools/file.py coding-agent/tests/tools/test_file.py
git commit -m "feat(p0): add file tools (read, write, replace) with path validation"
```

---

## Task 9: Shell Tool

**Files:**
- Create: `coding-agent/src/coding_agent/tools/shell.py`
- Test: `coding-agent/tests/tools/test_shell.py`

- [ ] **Step 1: Write the failing tests**

`coding-agent/tests/tools/test_shell.py`:

```python
from pathlib import Path

import pytest

from coding_agent.tools.shell import bash, register_shell_tools
from coding_agent.tools.registry import ToolRegistry


class TestBash:
    @pytest.mark.asyncio
    async def test_simple_command(self, tmp_path):
        result = await bash(cmd="echo hello", cwd=tmp_path)
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_command_with_exit_code(self, tmp_path):
        result = await bash(cmd="exit 1", cwd=tmp_path)
        assert "exit code: 1" in result.lower() or "exit_code" in result.lower()

    @pytest.mark.asyncio
    async def test_command_stderr(self, tmp_path):
        result = await bash(cmd="echo err >&2", cwd=tmp_path)
        assert "err" in result

    @pytest.mark.asyncio
    async def test_output_truncation(self, tmp_path):
        result = await bash(cmd="seq 1 100000", cwd=tmp_path, max_output=500)
        assert len(result) <= 600  # 500 + truncation message

    @pytest.mark.asyncio
    async def test_timeout(self, tmp_path):
        result = await bash(cmd="sleep 30", cwd=tmp_path, timeout=1)
        assert "timeout" in result.lower()


class TestRegisterShellTools:
    def test_registers_bash(self, tmp_path):
        reg = ToolRegistry()
        register_shell_tools(reg, cwd=tmp_path)
        names = {s.name for s in reg.schemas()}
        assert "bash" in names
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/tools/test_shell.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

`coding-agent/src/coding_agent/tools/shell.py`:

```python
"""Shell tool: execute bash commands with output capture."""

from __future__ import annotations

import asyncio
from functools import partial
from pathlib import Path

from coding_agent.tools.registry import ToolDef, ToolRegistry

_DEFAULT_TIMEOUT = 120  # seconds
_DEFAULT_MAX_OUTPUT = 50_000  # characters


async def bash(
    cmd: str,
    *,
    cwd: Path,
    timeout: int = _DEFAULT_TIMEOUT,
    max_output: int = _DEFAULT_MAX_OUTPUT,
) -> str:
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return f"Error: command timed out after {timeout}s"

        output = stdout.decode(errors="replace")

        if len(output) > max_output:
            output = output[:max_output] + f"\n... (truncated at {max_output} chars)"

        exit_code = proc.returncode
        if exit_code != 0:
            return f"{output}\n(exit_code: {exit_code})"
        return output

    except Exception as e:
        return f"Error executing command: {e}"


def register_shell_tools(registry: ToolRegistry, cwd: Path) -> None:
    registry.register(ToolDef(
        name="bash",
        description="Execute a bash command and return combined stdout/stderr",
        parameters={
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "The bash command to execute"},
            },
            "required": ["cmd"],
        },
        handler=partial(bash, cwd=cwd),
    ))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/tools/test_shell.py -v
```

Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add coding-agent/src/coding_agent/tools/shell.py coding-agent/tests/tools/test_shell.py
git commit -m "feat(p0): add bash tool with timeout and output truncation"
```

---

## Task 10: Search Tools (grep, glob)

**Files:**
- Create: `coding-agent/src/coding_agent/tools/search.py`
- Test: `coding-agent/tests/tools/test_search.py`

- [ ] **Step 1: Write the failing tests**

`coding-agent/tests/tools/test_search.py`:

```python
from pathlib import Path

import pytest

from coding_agent.tools.search import grep, glob_search, register_search_tools
from coding_agent.tools.registry import ToolRegistry


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "foo.py").write_text("def hello():\n    print('hello')\n")
    (tmp_path / "bar.py").write_text("def world():\n    print('world')\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "baz.py").write_text("import foo\nfoo.hello()\n")
    return tmp_path


class TestGrep:
    @pytest.mark.asyncio
    async def test_grep_finds_matches(self, repo):
        result = await grep(pattern="hello", repo_root=repo)
        assert "foo.py" in result
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_grep_no_match(self, repo):
        result = await grep(pattern="nonexistent_string_xyz", repo_root=repo)
        assert "no matches" in result.lower()

    @pytest.mark.asyncio
    async def test_grep_in_subdir(self, repo):
        result = await grep(pattern="import foo", repo_root=repo)
        assert "baz.py" in result


class TestGlobSearch:
    @pytest.mark.asyncio
    async def test_glob_py_files(self, repo):
        result = await glob_search(pattern="**/*.py", repo_root=repo)
        assert "foo.py" in result
        assert "bar.py" in result
        assert "baz.py" in result

    @pytest.mark.asyncio
    async def test_glob_no_match(self, repo):
        result = await glob_search(pattern="**/*.rs", repo_root=repo)
        assert "no matches" in result.lower()


class TestRegisterSearchTools:
    def test_registers_two_tools(self, repo):
        reg = ToolRegistry()
        register_search_tools(reg, repo_root=repo)
        names = {s.name for s in reg.schemas()}
        assert names == {"grep", "glob"}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/tools/test_search.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

`coding-agent/src/coding_agent/tools/search.py`:

```python
"""Search tools: grep (content search) and glob (file pattern matching)."""

from __future__ import annotations

import asyncio
from functools import partial
from pathlib import Path

from coding_agent.tools.registry import ToolDef, ToolRegistry

_MAX_RESULTS = 50


async def grep(pattern: str, *, repo_root: Path, max_results: int = _MAX_RESULTS) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "grep", "-rn", "--include=*", pattern, ".",
            cwd=str(repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode(errors="replace").strip()

        if not output:
            return f"No matches found for '{pattern}'"

        lines = output.split("\n")
        if len(lines) > max_results:
            lines = lines[:max_results]
            output = "\n".join(lines) + f"\n... ({len(lines)} of many matches shown)"
        else:
            output = "\n".join(lines)

        return output

    except asyncio.TimeoutError:
        return "Error: grep timed out"
    except Exception as e:
        return f"Error running grep: {e}"


async def glob_search(pattern: str, *, repo_root: Path, max_results: int = _MAX_RESULTS) -> str:
    try:
        matches = sorted(repo_root.glob(pattern))
        # Make paths relative
        results = []
        for m in matches:
            if m.is_file():
                results.append(str(m.relative_to(repo_root)))
            if len(results) >= max_results:
                break

        if not results:
            return f"No matches found for pattern '{pattern}'"

        return "\n".join(results)

    except Exception as e:
        return f"Error running glob: {e}"


def register_search_tools(registry: ToolRegistry, repo_root: Path) -> None:
    registry.register(ToolDef(
        name="grep",
        description="Search file contents for a pattern (regex supported)",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Search pattern (regex)"},
            },
            "required": ["pattern"],
        },
        handler=partial(grep, repo_root=repo_root),
    ))

    registry.register(ToolDef(
        name="glob",
        description="Find files matching a glob pattern (e.g. '**/*.py')",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern"},
            },
            "required": ["pattern"],
        },
        handler=partial(glob_search, repo_root=repo_root),
    ))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/tools/test_search.py -v
```

Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add coding-agent/src/coding_agent/tools/search.py coding-agent/tests/tools/test_search.py
git commit -m "feat(p0): add grep and glob search tools"
```

---

## Task 11: Wire Protocol + Headless Consumer

**Files:**
- Create: `coding-agent/src/coding_agent/core/wire.py`
- Create: `coding-agent/src/coding_agent/ui/__init__.py`
- Create: `coding-agent/src/coding_agent/ui/headless.py`
- Test: `coding-agent/tests/core/test_wire.py`

- [ ] **Step 1: Write the failing test**

`coding-agent/tests/core/test_wire.py`:

```python
import pytest

from coding_agent.core.wire import WireConsumer, WireMessage
from coding_agent.ui.headless import HeadlessConsumer


class TestWireMessage:
    def test_create(self):
        msg = WireMessage(type="turn_begin", data={"input": "hello"})
        assert msg.type == "turn_begin"
        assert msg.data == {"input": "hello"}


class TestHeadlessConsumer:
    def test_is_wire_consumer(self):
        consumer = HeadlessConsumer()
        assert isinstance(consumer, WireConsumer)

    @pytest.mark.asyncio
    async def test_on_message_no_error(self):
        consumer = HeadlessConsumer()
        msg = WireMessage(type="test", data={})
        await consumer.on_message(msg)  # should not raise

    @pytest.mark.asyncio
    async def test_request_approval_always_true(self):
        consumer = HeadlessConsumer()
        assert await consumer.request_approval("bash", {"cmd": "ls"}) is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_wire.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the WireMessage and WireConsumer protocol**

`coding-agent/src/coding_agent/core/wire.py`:

```python
"""Wire protocol types. Shared by all consumers (TUI, headless, HTTP, ACP)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class WireMessage:
    """Minimal wire message for P0. Full WireMessage protocol in P2."""
    type: str
    data: dict[str, Any]


@runtime_checkable
class WireConsumer(Protocol):
    """Any consumer that can receive wire messages and handle approvals."""

    async def on_message(self, msg: WireMessage) -> None: ...
    async def request_approval(self, tool: str, args: dict) -> bool: ...
```

- [ ] **Step 4: Write the HeadlessConsumer**

`coding-agent/src/coding_agent/ui/__init__.py`:

```python
```

`coding-agent/src/coding_agent/ui/headless.py`:

```python
"""Headless consumer: batch mode output, auto-approves everything."""

from __future__ import annotations

from typing import Any

import structlog

from coding_agent.core.wire import WireMessage

logger = structlog.get_logger()


class HeadlessConsumer:
    """Batch-mode wire consumer. Logs events, auto-approves everything."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    async def on_message(self, msg: WireMessage) -> None:
        if self.verbose:
            logger.info("wire_event", type=msg.type, data=msg.data)

    async def request_approval(self, tool: str, args: dict) -> bool:
        """In headless mode, always approve (yolo)."""
        return True
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_wire.py -v
```

Expected: PASS — all 4 tests green

- [ ] **Step 6: Commit**

```bash
git add coding-agent/src/coding_agent/core/wire.py coding-agent/src/coding_agent/ui/ tests/core/test_wire.py
git commit -m "feat(p0): add WireMessage, WireConsumer protocol, HeadlessConsumer"
```

---

## Task 12: Agent Loop (The Kernel)

**Files:**
- Create: `coding-agent/src/coding_agent/core/loop.py`
- Test: `coding-agent/tests/core/test_loop.py`

- [ ] **Step 1: Write the failing tests**

`coding-agent/tests/core/test_loop.py`:

```python
import json
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock

import pytest

from coding_agent.core.context import Context
from coding_agent.core.doom import DoomDetector
from coding_agent.core.loop import AgentLoop, TurnOutcome
from coding_agent.core.tape import Tape
from coding_agent.core.wire import WireMessage
from coding_agent.providers.base import StreamEvent, ToolCallEvent, ToolSchema
from coding_agent.tools.registry import ToolDef, ToolRegistry
from coding_agent.ui.headless import HeadlessConsumer


class MockProvider:
    """Provider that returns scripted responses."""

    def __init__(self, responses: list[list[StreamEvent]]):
        self._responses = iter(responses)
        self._model = "mock-model"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def max_context_size(self) -> int:
        return 100_000

    async def stream(
        self, messages: list[dict], tools: list[ToolSchema] | None = None
    ) -> AsyncIterator[StreamEvent]:
        for event in next(self._responses):
            yield event


def _text_response(text: str) -> list[StreamEvent]:
    return [
        StreamEvent(type="delta", text=text),
        StreamEvent(type="done"),
    ]


def _tool_response(call_id: str, tool: str, args: dict) -> list[StreamEvent]:
    return [
        StreamEvent(
            type="tool_call",
            tool_calls=[ToolCallEvent(call_id=call_id, tool=tool, args=args)],
        ),
        StreamEvent(type="done"),
    ]


def _make_loop(tmp_path, responses, tools=None) -> AgentLoop:
    tape = Tape(tmp_path / "test.jsonl")
    provider = MockProvider(responses)
    registry = tools or ToolRegistry()
    context = Context(max_tokens=100_000, system_prompt="You are a test agent.")
    consumer = HeadlessConsumer()
    return AgentLoop(
        provider=provider,
        tools=registry,
        tape=tape,
        context=context,
        consumer=consumer,
        max_steps=10,
        doom_threshold=3,
    )


class TestAgentLoop:
    @pytest.mark.asyncio
    async def test_simple_text_response(self, tmp_path):
        loop = _make_loop(tmp_path, [_text_response("Hello!")])
        result = await loop.run_turn("Say hello")
        assert result.stop_reason == "no_tool_calls"
        assert result.final_message == "Hello!"

    @pytest.mark.asyncio
    async def test_tool_call_then_response(self, tmp_path):
        async def echo(text: str) -> str:
            return f"echoed: {text}"

        registry = ToolRegistry()
        registry.register(ToolDef(
            name="echo",
            description="Echo",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}},
            handler=echo,
        ))

        loop = _make_loop(
            tmp_path,
            [
                _tool_response("c1", "echo", {"text": "hi"}),
                _text_response("Done"),
            ],
            tools=registry,
        )
        result = await loop.run_turn("Echo hi")
        assert result.stop_reason == "no_tool_calls"
        assert result.final_message == "Done"

        # Verify tape has the full history
        entries = loop.tape.entries()
        kinds = [e.kind for e in entries]
        assert "message" in kinds  # user message
        assert "tool_call" in kinds
        assert "tool_result" in kinds

    @pytest.mark.asyncio
    async def test_max_steps_reached(self, tmp_path):
        # Provider always returns tool calls → should hit max_steps
        responses = [_tool_response(f"c{i}", "echo", {"text": "loop"}) for i in range(15)]

        async def echo(text: str) -> str:
            return "echoed"

        registry = ToolRegistry()
        registry.register(ToolDef(
            name="echo",
            description="Echo",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}},
            handler=echo,
        ))

        loop = _make_loop(tmp_path, responses, tools=registry)
        loop.max_steps = 3
        result = await loop.run_turn("Loop forever")
        assert result.stop_reason == "max_steps_reached"

    @pytest.mark.asyncio
    async def test_doom_loop_detected(self, tmp_path):
        # Same tool call 3 times → doom loop
        responses = [
            _tool_response("c1", "bash", {"cmd": "ls"}),
            _tool_response("c2", "bash", {"cmd": "ls"}),
            _tool_response("c3", "bash", {"cmd": "ls"}),
        ]

        async def bash(cmd: str) -> str:
            return "output"

        registry = ToolRegistry()
        registry.register(ToolDef(
            name="bash",
            description="Bash",
            parameters={"type": "object", "properties": {"cmd": {"type": "string"}}},
            handler=bash,
        ))

        loop = _make_loop(tmp_path, responses, tools=registry)
        result = await loop.run_turn("Do something")
        assert result.stop_reason == "doom_loop"

    @pytest.mark.asyncio
    async def test_tape_records_user_message(self, tmp_path):
        loop = _make_loop(tmp_path, [_text_response("Hi")])
        await loop.run_turn("Hello agent")
        entries = loop.tape.entries()
        assert entries[0].kind == "message"
        assert entries[0].payload["role"] == "user"
        assert entries[0].payload["content"] == "Hello agent"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_loop.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

`coding-agent/src/coding_agent/core/loop.py`:

```python
"""AgentLoop: the while-loop kernel that drives the agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from coding_agent.core.context import Context
from coding_agent.core.doom import DoomDetector
from coding_agent.core.tape import Entry, Tape
from coding_agent.core.wire import WireConsumer, WireMessage
from coding_agent.providers.base import StreamEvent
from coding_agent.tools.registry import ToolRegistry

logger = structlog.get_logger()


@dataclass
class TurnOutcome:
    stop_reason: str  # "no_tool_calls", "max_steps_reached", "doom_loop"
    final_message: str | None = None
    steps: int = 0


class AgentLoop:
    def __init__(
        self,
        provider: Any,  # ChatProvider
        tools: ToolRegistry,
        tape: Tape,
        context: Context,
        consumer: WireConsumer,
        max_steps: int = 30,
        doom_threshold: int = 3,
    ):
        self.provider = provider
        self.tools = tools
        self.tape = tape
        self.context = context
        self.consumer = consumer
        self.max_steps = max_steps
        self.doom = DoomDetector(threshold=doom_threshold)

    async def run_turn(self, user_input: str) -> TurnOutcome:
        """Execute a single conversation turn."""
        self.tape.append(Entry.message("user", user_input))
        await self.consumer.on_message(
            WireMessage(type="turn_begin", data={"input": user_input})
        )

        for step in range(1, self.max_steps + 1):
            logger.info("agent_step", step=step, max_steps=self.max_steps)

            # Build context from tape
            messages = self.context.build_working_set(self.tape)

            # Call LLM
            text_buffer = ""
            tool_calls = []

            async for event in self.provider.stream(
                messages=messages, tools=self.tools.schemas() or None
            ):
                match event.type:
                    case "delta":
                        text_buffer += event.text or ""
                    case "tool_call":
                        tool_calls = event.tool_calls
                    case "error":
                        logger.error("provider_error", error=event.error)
                        return TurnOutcome(
                            stop_reason="error",
                            final_message=f"Provider error: {event.error}",
                            steps=step,
                        )

            # No tool calls → turn complete
            if not tool_calls:
                self.tape.append(Entry.message("assistant", text_buffer))
                await self.consumer.on_message(
                    WireMessage(type="turn_end", data={"text": text_buffer})
                )
                return TurnOutcome(
                    stop_reason="no_tool_calls",
                    final_message=text_buffer,
                    steps=step,
                )

            # Execute tool calls
            for call in tool_calls:
                # Record tool call to tape
                self.tape.append(
                    Entry.tool_call(call.call_id, call.tool, call.args)
                )

                # Doom loop check
                if self.doom.observe(call.tool, call.args):
                    self.tape.append(
                        Entry.tool_result(
                            call.call_id,
                            "[ABORTED] Repetitive tool call detected",
                        )
                    )
                    logger.warning(
                        "doom_loop_detected",
                        tool=call.tool,
                        count=self.doom.count,
                    )
                    return TurnOutcome(
                        stop_reason="doom_loop",
                        steps=step,
                    )

                # Execute tool
                await self.consumer.on_message(
                    WireMessage(
                        type="tool_call_begin",
                        data={"tool": call.tool, "args": call.args},
                    )
                )

                result = await self.tools.execute(call.tool, call.args)
                self.tape.append(Entry.tool_result(call.call_id, result))

                await self.consumer.on_message(
                    WireMessage(
                        type="tool_call_end",
                        data={"tool": call.tool, "result": result[:200]},
                    )
                )

        # Exhausted max_steps
        return TurnOutcome(stop_reason="max_steps_reached", steps=self.max_steps)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_loop.py -v
```

Expected: All 5 tests PASS.

- [ ] **Step 5: Run ALL tests to verify nothing broke**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/ -v
```

Expected: All tests across all modules PASS.

- [ ] **Step 6: Commit**

```bash
git add coding-agent/src/coding_agent/core/loop.py coding-agent/tests/core/test_loop.py
git commit -m "feat(p0): add AgentLoop kernel with tool dispatch and doom detection"
```

---

## Task 13: Smoke Test (End-to-End with Mock Provider)

**Files:**
- Test: `coding-agent/tests/test_e2e_smoke.py`

- [ ] **Step 1: Write the integration test**

`coding-agent/tests/test_e2e_smoke.py`:

```python
"""End-to-end smoke test: full agent loop with mock provider + real file tools."""

from pathlib import Path

import pytest

from coding_agent.core.context import Context
from coding_agent.core.loop import AgentLoop
from coding_agent.core.tape import Tape
from coding_agent.providers.base import StreamEvent, ToolCallEvent, ToolSchema
from coding_agent.tools.registry import ToolRegistry
from coding_agent.tools.file import register_file_tools
from coding_agent.tools.shell import register_shell_tools
from coding_agent.tools.search import register_search_tools
from coding_agent.ui.headless import HeadlessConsumer


class ScriptedProvider:
    """Provider that plays back a scripted conversation."""

    def __init__(self, script: list):
        self._script = iter(script)

    @property
    def model_name(self) -> str:
        return "scripted"

    @property
    def max_context_size(self) -> int:
        return 100_000

    async def stream(self, messages, tools=None):
        step = next(self._script)
        for event in step:
            yield event


class TestE2ESmoke:
    @pytest.mark.asyncio
    async def test_read_and_respond(self, tmp_path):
        """Agent reads a file and summarizes it."""
        (tmp_path / "hello.py").write_text("print('hello world')")

        script = [
            # Step 1: Agent calls file_read
            [
                StreamEvent(
                    type="tool_call",
                    tool_calls=[
                        ToolCallEvent(
                            call_id="c1",
                            tool="file_read",
                            args={"path": "hello.py"},
                        )
                    ],
                ),
                StreamEvent(type="done"),
            ],
            # Step 2: Agent responds with text
            [
                StreamEvent(type="delta", text="The file prints hello world."),
                StreamEvent(type="done"),
            ],
        ]

        registry = ToolRegistry()
        register_file_tools(registry, repo_root=tmp_path)

        tape = Tape(tmp_path / "tape.jsonl")
        loop = AgentLoop(
            provider=ScriptedProvider(script),
            tools=registry,
            tape=tape,
            context=Context(100_000, "You are a coding agent."),
            consumer=HeadlessConsumer(),
            max_steps=10,
        )

        result = await loop.run_turn("Read hello.py and describe it")
        assert result.stop_reason == "no_tool_calls"
        assert "hello world" in result.final_message

        # Verify tape has the full trace
        entries = tape.entries()
        kinds = [e.kind for e in entries]
        assert kinds == ["message", "tool_call", "tool_result", "message"]

    @pytest.mark.asyncio
    async def test_edit_file(self, tmp_path):
        """Agent edits a file via file_replace."""
        (tmp_path / "app.py").write_text("x = 1\n")

        script = [
            # Step 1: Agent calls file_replace
            [
                StreamEvent(
                    type="tool_call",
                    tool_calls=[
                        ToolCallEvent(
                            call_id="c1",
                            tool="file_replace",
                            args={
                                "path": "app.py",
                                "old_string": "x = 1",
                                "new_string": "x = 42",
                            },
                        )
                    ],
                ),
                StreamEvent(type="done"),
            ],
            # Step 2: Text response
            [
                StreamEvent(type="delta", text="Changed x to 42."),
                StreamEvent(type="done"),
            ],
        ]

        registry = ToolRegistry()
        register_file_tools(registry, repo_root=tmp_path)

        tape = Tape(tmp_path / "tape.jsonl")
        loop = AgentLoop(
            provider=ScriptedProvider(script),
            tools=registry,
            tape=tape,
            context=Context(100_000, "You are a coding agent."),
            consumer=HeadlessConsumer(),
            max_steps=10,
        )

        result = await loop.run_turn("Change x to 42")
        assert result.stop_reason == "no_tool_calls"
        assert (tmp_path / "app.py").read_text() == "x = 42\n"
```

- [ ] **Step 2: Run the smoke test**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/test_e2e_smoke.py -v
```

Expected: Both tests PASS.

- [ ] **Step 3: Run full test suite**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/ -v --tb=short
```

Expected: All tests PASS (should be ~40+ tests total).

- [ ] **Step 4: Commit**

```bash
git add coding-agent/tests/test_e2e_smoke.py
git commit -m "test(p0): add E2E smoke tests with scripted provider + real file tools"
```

---

## Task 14: CLI Verification

- [ ] **Step 1: Verify CLI help works**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run python -m coding_agent --help
uv run python -m coding_agent run --help
```

Expected: Help text displays with all options.

- [ ] **Step 2: Verify CLI run (with real API, optional)**

If you have an API key available:

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
AGENT_API_KEY="your-key" uv run python -m coding_agent run \
    --goal "Read the pyproject.toml and tell me the project name" \
    --repo . \
    --model gpt-4o-mini \
    --max-steps 5
```

Expected: Agent reads pyproject.toml and responds with "coding-agent".

- [ ] **Step 3: Final commit with all init files**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop
git add coding-agent/
git commit -m "feat(p0): complete agent kernel MVP

Agent can receive a goal, read files, execute commands, edit files
via search-and-replace, detect doom loops, and produce results.

Components: Config, Tape (JSONL), Context, DoomDetector,
ChatProvider (OpenAI-compatible), ToolRegistry, file/shell/search tools,
HeadlessConsumer, AgentLoop kernel."
```

---

## Summary

| Task | Component | Tests | Est. LOC |
|------|-----------|-------|----------|
| 1 | Project scaffolding | — | 80 |
| 2 | Config | 7 | 80 |
| 3 | Tape | 12 | 150 |
| 4 | Context | 4 | 80 |
| 5 | DoomDetector | 6 | 40 |
| 6 | ChatProvider + OpenAI | 4 | 150 |
| 7 | ToolRegistry | 5 | 50 |
| 8 | File tools | 12 | 130 |
| 9 | Shell tool | 6 | 50 |
| 10 | Search tools | 6 | 70 |
| 11 | Wire Protocol + HeadlessConsumer | 4 | 50 |
| 12 | AgentLoop | 5 | 120 |
| 13 | E2E smoke tests | 2 | 80 |
| 14 | CLI verification | — | — |
| **Total** | | **~73** | **~1,130** |

P0 exit criteria: `uv run python -m coding_agent run --goal "..." --repo .` works end-to-end.
