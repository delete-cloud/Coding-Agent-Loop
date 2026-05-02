from __future__ import annotations

import json
import textwrap
from pathlib import Path

from agentkit.environment import Environment
from coding_agent.environment import LocalEnvironment


def test_local_environment_satisfies_environment_protocol(tmp_path: Path) -> None:
    env: Environment = LocalEnvironment(tmp_path)

    assert env.kind == "local"
    assert env.tool_config() == {"workspace_root": str(tmp_path.resolve())}


def test_environment_file_tools_return_expected_callables(tmp_path: Path) -> None:
    env: Environment = LocalEnvironment(tmp_path)
    target = tmp_path / "note.txt"
    target.write_text("hello")

    file_read, file_write, file_replace, glob_files, grep_search = (
        env.build_file_tools()
    )

    assert file_read("note.txt") == "hello"
    assert "Written 7 bytes" in file_write("out.txt", "created")
    assert (tmp_path / "out.txt").read_text() == "created"
    assert file_replace("out.txt", "created", "updated") == "Replaced in out.txt"
    assert (tmp_path / "out.txt").read_text() == "updated"
    assert str(target) in glob_files("*.txt", ".")
    assert "note.txt:1:hello" in str(grep_search("hello", ".", ""))


def test_environment_file_patch_tool_returns_callable(tmp_path: Path) -> None:
    env: Environment = LocalEnvironment(tmp_path)
    target = tmp_path / "hello.py"
    target.write_text("def greet():\n    return 'hello'\n")
    patch = textwrap.dedent(
        """\
        @@ -1,2 +1,2 @@
         def greet():
        -    return 'hello'
        +    return 'hello world'
        """
    )

    result = env.build_file_patch_tool()("hello.py", patch)
    payload = json.loads(result)

    assert payload["success"] is True
    assert "hello world" in target.read_text()


def test_environment_shell_tool_returns_callable(tmp_path: Path) -> None:
    env: Environment = LocalEnvironment(tmp_path)
    shell_tool = env.build_shell_tool()

    result = shell_tool("pwd", cwd=str(tmp_path))

    assert str(tmp_path.resolve()) in str(result)
