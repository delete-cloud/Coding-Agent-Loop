from __future__ import annotations

import json
import textwrap
from pathlib import Path

from coding_agent.tools.file_patch_tool import build_file_patch_tool


def test_file_patch_dry_run_reports_context_failure_without_mutation(
    tmp_path: Path,
) -> None:
    target = tmp_path / "app.py"
    target.write_text("def greet():\n    return 'hello'\n", encoding="utf-8")
    patch_tool = build_file_patch_tool(tmp_path)
    patch = textwrap.dedent(
        """\
        @@ -1,2 +1,2 @@
         def greet():
        -    return 'missing'
        +    return 'patched'
        """
    )

    payload = json.loads(patch_tool("app.py", patch, dry_run=True))

    assert payload == {
        "success": False,
        "error": "Context not found for hunk",
        "path": "app.py",
        "dry_run": True,
    }
    assert target.read_text(encoding="utf-8") == "def greet():\n    return 'hello'\n"


def test_file_patch_dry_run_reports_plan_without_mutation(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("def greet():\n    return 'hello'\n", encoding="utf-8")
    patch_tool = build_file_patch_tool(tmp_path)
    patch = textwrap.dedent(
        """\
        @@ -1,2 +1,2 @@
         def greet():
        -    return 'hello'
        +    return 'patched'
        """
    )

    payload = json.loads(patch_tool("app.py", patch, dry_run=True))

    assert payload["success"] is True
    assert payload["dry_run"] is True
    assert payload["changed"] is True
    assert payload["plan"]["path"] == "app.py"
    assert payload["plan"]["operation"] == "modify"
    assert payload["plan"]["additions"] == 1
    assert payload["plan"]["deletions"] == 1
    assert "patched" not in json.dumps(payload)
    assert target.read_text(encoding="utf-8") == "def greet():\n    return 'hello'\n"


def test_file_patch_apply_preserves_existing_behavior_and_reports_not_dry_run(
    tmp_path: Path,
) -> None:
    target = tmp_path / "app.py"
    target.write_text("def greet():\n    return 'hello'\n", encoding="utf-8")
    patch_tool = build_file_patch_tool(tmp_path)
    patch = textwrap.dedent(
        """\
        @@ -1,2 +1,2 @@
         def greet():
        -    return 'hello'
        +    return 'patched'
        """
    )

    payload = json.loads(patch_tool("app.py", patch))

    assert payload["success"] is True
    assert payload["dry_run"] is False
    assert payload["changed"] is True
    assert target.read_text(encoding="utf-8") == "def greet():\n    return 'patched'\n"


def test_file_patch_accepts_single_file_git_diff_in_dry_run_and_apply(
    tmp_path: Path,
) -> None:
    target = tmp_path / "app.py"
    target.write_text("def greet():\n    return 'hello'\n", encoding="utf-8")
    patch_tool = build_file_patch_tool(tmp_path)
    patch = textwrap.dedent(
        """\
        diff --git a/app.py b/app.py
        index 1111111..2222222 100644
        --- a/app.py
        +++ b/app.py
        @@ -1,2 +1,2 @@
         def greet():
        -    return 'hello'
        +    return 'patched'
        """
    )

    dry_run_payload = json.loads(patch_tool("app.py", patch, dry_run=True))

    assert dry_run_payload["success"] is True
    assert dry_run_payload["dry_run"] is True
    assert target.read_text(encoding="utf-8") == "def greet():\n    return 'hello'\n"

    apply_payload = json.loads(patch_tool("app.py", patch))

    assert target.read_text(encoding="utf-8") == "def greet():\n    return 'patched'\n"
    assert apply_payload["success"] is True
    assert apply_payload["dry_run"] is False
    assert apply_payload["changed"] is True


def test_file_patch_rejects_safe_edit_policy_denial(tmp_path: Path) -> None:
    target = tmp_path / "binary.bin"
    target.write_bytes(b"\x00binary")
    patch_tool = build_file_patch_tool(tmp_path)
    patch = "@@ -1,1 +1,1 @@\n-binary\n+text\n"

    payload = json.loads(patch_tool("binary.bin", patch, dry_run=True))

    assert payload["success"] is False
    assert payload["dry_run"] is True
    assert payload["reason"] == "binary_file"
    assert target.read_bytes() == b"\x00binary"
