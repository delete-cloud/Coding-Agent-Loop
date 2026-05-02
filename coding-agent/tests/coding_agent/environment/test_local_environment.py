from __future__ import annotations

import json
import textwrap
from pathlib import Path

from coding_agent.environment import LocalEnvironment


def test_local_environment_resolves_workspace_root(tmp_path: Path) -> None:
    env = LocalEnvironment(workspace_root=tmp_path / "repo")

    assert env.kind == "local"
    assert env.workspace_root == (tmp_path / "repo").resolve()
    assert env.tool_config() == {"workspace_root": str((tmp_path / "repo").resolve())}


def test_local_environment_file_tools_preserve_path_confinement(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    target = workspace / "note.txt"
    target.write_text("inside")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    env = LocalEnvironment(workspace_root=workspace)

    file_read, _, _, _, _ = env.build_file_tools()

    assert file_read("note.txt") == "inside"
    assert "outside workspace" in str(file_read(str(outside))).lower()


def test_local_environment_file_patch_tool_uses_workspace_root(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    target = workspace / "hello.py"
    target.write_text("def greet():\n    return 'hello'\n")
    env = LocalEnvironment(workspace_root=workspace)
    patch_tool = env.build_file_patch_tool()
    patch = textwrap.dedent(
        """\
        @@ -1,2 +1,2 @@
         def greet():
        -    return 'hello'
        +    return 'hello world'
        """
    )

    result = patch_tool("hello.py", patch)
    payload = json.loads(result)

    assert payload["success"] is True
    assert "hello world" in target.read_text()
