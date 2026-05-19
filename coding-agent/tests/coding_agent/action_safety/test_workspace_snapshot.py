from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.action_safety import (
    WorkspaceSnapshot,
    WorkspaceSnapshotEntry,
    create_workspace_snapshot,
    restore_workspace_snapshot,
)


def test_workspace_snapshot_restore_preserves_git_and_recovers_modified_files(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    (workspace / ".git").mkdir(parents=True)
    _ = (workspace / ".git" / "HEAD").write_text(
        "ref: refs/heads/main\n",
        encoding="utf-8",
    )
    _ = (workspace / "README.md").write_text("before\n", encoding="utf-8")
    (workspace / "pkg").mkdir()
    _ = (workspace / "pkg" / "app.py").write_text("print('before')\n", encoding="utf-8")

    snapshot = create_workspace_snapshot(workspace, tmp_path / "snapshot")

    _ = (workspace / "README.md").write_text("after\n", encoding="utf-8")
    (workspace / "pkg" / "app.py").unlink()
    _ = (workspace / "stale.txt").write_text("remove me\n", encoding="utf-8")
    _ = (workspace / ".git" / "HEAD").write_text(
        "ref: refs/heads/feature\n",
        encoding="utf-8",
    )

    restore_workspace_snapshot(snapshot, workspace)

    assert (workspace / "README.md").read_text(encoding="utf-8") == "before\n"
    assert (workspace / "pkg" / "app.py").read_text(
        encoding="utf-8"
    ) == "print('before')\n"
    assert not (workspace / "stale.txt").exists()
    assert (workspace / ".git" / "HEAD").read_text(
        encoding="utf-8"
    ) == "ref: refs/heads/feature\n"
    assert snapshot.file_count == 2
    assert [entry.path for entry in snapshot.entries] == ["README.md", "pkg/app.py"]


def test_workspace_snapshot_restore_rejects_invalid_snapshot_without_clearing_target(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _ = (workspace / "keep.txt").write_text("keep\n", encoding="utf-8")
    snapshot_root = tmp_path / "snapshot"
    snapshot = create_workspace_snapshot(workspace, snapshot_root)
    _ = (snapshot.files_root / "unexpected.txt").write_text(
        "tampered\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="manifest"):
        restore_workspace_snapshot(snapshot, workspace)

    assert (workspace / "keep.txt").read_text(encoding="utf-8") == "keep\n"


def test_workspace_snapshot_restore_rejects_same_size_content_tampering_without_clearing_target(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _ = (workspace / "keep.txt").write_text("keep\n", encoding="utf-8")
    snapshot = create_workspace_snapshot(workspace, tmp_path / "snapshot")
    _ = (snapshot.files_root / "keep.txt").write_text("evil\n", encoding="utf-8")

    with pytest.raises(ValueError, match="hash"):
        restore_workspace_snapshot(snapshot, workspace)

    assert (workspace / "keep.txt").read_text(encoding="utf-8") == "keep\n"


def test_workspace_snapshot_restore_rejects_path_tampering_without_clearing_target(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _ = (workspace / "keep.txt").write_text("keep\n", encoding="utf-8")
    snapshot = create_workspace_snapshot(workspace, tmp_path / "snapshot")
    (snapshot.files_root / "keep.txt").rename(snapshot.files_root / "evil.txt")

    with pytest.raises(ValueError, match="manifest"):
        restore_workspace_snapshot(snapshot, workspace)

    assert (workspace / "keep.txt").read_text(encoding="utf-8") == "keep\n"


def test_workspace_snapshot_restore_rejects_snapshot_symlink_without_clearing_target(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _ = (workspace / "keep.txt").write_text("keep\n", encoding="utf-8")
    snapshot = create_workspace_snapshot(workspace, tmp_path / "snapshot")
    (snapshot.files_root / "keep.txt").unlink()
    (snapshot.files_root / "keep.txt").symlink_to("/etc/passwd")

    with pytest.raises(ValueError, match="symlinks"):
        restore_workspace_snapshot(snapshot, workspace)

    assert (workspace / "keep.txt").read_text(encoding="utf-8") == "keep\n"


def test_workspace_snapshot_create_rejects_symlink(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "link").symlink_to("/etc/passwd")

    with pytest.raises(ValueError, match="symlinks"):
        _ = create_workspace_snapshot(workspace, tmp_path / "snapshot")


def test_workspace_snapshot_restore_rejects_preserved_root_members(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _ = (workspace / "keep.txt").write_text("keep\n", encoding="utf-8")
    snapshot_root = tmp_path / "snapshot"
    files_root = snapshot_root / "files"
    (files_root / ".git").mkdir(parents=True)
    _ = (files_root / ".git" / "HEAD").write_text("bad\n", encoding="utf-8")
    snapshot = WorkspaceSnapshot(
        snapshot_root=snapshot_root,
        files_root=files_root,
        file_count=1,
        total_bytes=4,
        entries=(
            WorkspaceSnapshotEntry(
                path=".git/HEAD",
                size_bytes=4,
                sha256="0" * 64,
            ),
        ),
    )

    with pytest.raises(ValueError, match="preserved root"):
        restore_workspace_snapshot(snapshot, workspace)

    assert (workspace / "keep.txt").read_text(encoding="utf-8") == "keep\n"


def test_workspace_snapshot_restore_rejects_snapshot_inside_workspace_without_clearing_target(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _ = (workspace / "keep.txt").write_text("keep\n", encoding="utf-8")
    snapshot = create_workspace_snapshot(workspace, workspace / ".snapshot")

    with pytest.raises(ValueError, match="inside workspace"):
        restore_workspace_snapshot(snapshot, workspace)

    assert (workspace / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert snapshot.files_root.exists()
