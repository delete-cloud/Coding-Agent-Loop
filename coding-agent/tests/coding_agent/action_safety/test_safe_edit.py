from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.action_safety import SafeEditReason, validate_safe_edit_path


def test_safe_edit_policy_allows_existing_text_file(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    target = workspace / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("print('ok')\n", encoding="utf-8")

    decision = validate_safe_edit_path(workspace, "src/app.py")

    assert decision.allowed is True
    assert decision.reason == SafeEditReason.OK
    assert decision.normalized_path == str(target)
    assert decision.size_bytes == len("print('ok')\n")
    assert "print" not in str(decision.to_safe_dict())


def test_safe_edit_policy_rejects_workspace_escape_symlink_binary_and_oversized_file(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    link = workspace / "escape-link"
    link.symlink_to(outside)
    binary = workspace / "binary.bin"
    binary.write_bytes(b"\x00binary")
    oversized = workspace / "large.txt"
    oversized.write_text("x" * 11, encoding="utf-8")

    escape_decision = validate_safe_edit_path(workspace, "../outside.txt")
    symlink_decision = validate_safe_edit_path(workspace, "escape-link")
    binary_decision = validate_safe_edit_path(workspace, "binary.bin")
    oversized_decision = validate_safe_edit_path(
        workspace,
        "large.txt",
        max_file_bytes=10,
    )

    assert escape_decision.allowed is False
    assert escape_decision.reason == SafeEditReason.WORKSPACE_ESCAPE
    assert symlink_decision.allowed is False
    assert symlink_decision.reason == SafeEditReason.SYMLINK
    assert binary_decision.allowed is False
    assert binary_decision.reason == SafeEditReason.BINARY_FILE
    assert oversized_decision.allowed is False
    assert oversized_decision.reason == SafeEditReason.FILE_TOO_LARGE
    assert oversized_decision.size_bytes == 11


def test_safe_edit_policy_rejects_symlink_parent_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    link_dir = workspace / "linked-dir"
    link_dir.symlink_to(outside_dir, target_is_directory=True)

    decision = validate_safe_edit_path(
        workspace,
        "linked-dir/new.txt",
        allow_create=True,
    )

    assert decision.allowed is False
    assert decision.reason == SafeEditReason.SYMLINK


def test_safe_edit_policy_handles_missing_files_and_create_targets(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()

    missing = validate_safe_edit_path(workspace, "new.txt")
    create = validate_safe_edit_path(workspace, "new.txt", allow_create=True)
    missing_parent = validate_safe_edit_path(
        workspace,
        "missing/new.txt",
        allow_create=True,
    )

    assert missing.allowed is False
    assert missing.reason == SafeEditReason.MISSING_FILE
    assert create.allowed is True
    assert create.reason == SafeEditReason.OK
    assert missing_parent.allowed is False
    assert missing_parent.reason == SafeEditReason.MISSING_PARENT


def test_safe_edit_policy_rejects_create_under_existing_file(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    parent_file = workspace / "not-a-dir"
    parent_file.write_text("plain file\n", encoding="utf-8")

    decision = validate_safe_edit_path(
        workspace,
        "not-a-dir/new.txt",
        allow_create=True,
    )

    assert decision.allowed is False
    assert decision.reason == SafeEditReason.NOT_FILE


def test_safe_edit_policy_rejects_directories_and_invalid_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    directory = workspace / "pkg"
    directory.mkdir()

    directory_decision = validate_safe_edit_path(workspace, "pkg")

    assert directory_decision.allowed is False
    assert directory_decision.reason == SafeEditReason.NOT_FILE
    with pytest.raises(ValueError, match="workspace root does not exist"):
        validate_safe_edit_path(tmp_path / "missing", "file.txt")
