from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import shutil


_PRESERVED_ROOT_NAMES = frozenset({".git"})
_EXCLUDED_DIRECTORY_NAMES = frozenset({"__pycache__"})
_EXCLUDED_FILE_SUFFIXES = (".pyc", ".pyo")


@dataclass(frozen=True)
class WorkspaceSnapshotEntry:
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class WorkspaceSnapshot:
    snapshot_root: Path
    files_root: Path
    file_count: int
    total_bytes: int
    entries: tuple[WorkspaceSnapshotEntry, ...]


def create_workspace_snapshot(
    workspace_root: Path | str,
    snapshot_root: Path | str,
) -> WorkspaceSnapshot:
    root = Path(workspace_root).expanduser().resolve()
    target = Path(snapshot_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"workspace root does not exist: {root}")
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"snapshot root must be empty: {target}")
    target.mkdir(parents=True, exist_ok=True)
    files_root = target / "files"
    files_root.mkdir()

    file_count = 0
    total_bytes = 0
    entries: list[WorkspaceSnapshotEntry] = []
    for source in sorted(root.rglob("*")):
        relative = source.relative_to(root)
        if _should_exclude_snapshot_path(relative):
            continue
        if source.is_symlink():
            raise ValueError(
                f"workspace snapshot does not support symlinks: {relative}"
            )
        if source.is_dir():
            (files_root / relative).mkdir(parents=True, exist_ok=True)
            continue
        if not source.is_file():
            raise ValueError(
                f"workspace snapshot only supports regular files: {relative}"
            )
        destination = files_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copy2(source, destination)
        size_bytes = source.stat().st_size
        file_count += 1
        total_bytes += size_bytes
        entries.append(
            WorkspaceSnapshotEntry(
                path=relative.as_posix(),
                size_bytes=size_bytes,
                sha256=_sha256_file(source),
            )
        )

    return WorkspaceSnapshot(
        snapshot_root=target,
        files_root=files_root,
        file_count=file_count,
        total_bytes=total_bytes,
        entries=tuple(entries),
    )


def restore_workspace_snapshot(
    snapshot: WorkspaceSnapshot,
    workspace_root: Path | str,
) -> None:
    root = Path(workspace_root).expanduser().resolve()
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise ValueError(f"workspace root is not a directory: {root}")
    _reject_nested_snapshot_and_workspace(snapshot, root)

    _validate_snapshot(snapshot)
    _clear_workspace_except_preserved_roots(root)
    _copy_snapshot_files(snapshot.files_root, root)


def _validate_snapshot(snapshot: WorkspaceSnapshot) -> None:
    snapshot_root = snapshot.snapshot_root.expanduser().resolve()
    files_root = snapshot.files_root.expanduser().resolve()
    try:
        _ = files_root.relative_to(snapshot_root)
    except ValueError as exc:
        raise ValueError("workspace snapshot files root escapes snapshot root") from exc
    if files_root.is_symlink():
        raise ValueError("workspace snapshot files root must not be a symlink")
    if not files_root.is_dir():
        raise ValueError("workspace snapshot files root does not exist")

    manifest: dict[str, WorkspaceSnapshotEntry] = {}
    for entry in snapshot.entries:
        if entry.path in manifest:
            raise ValueError("workspace snapshot manifest contains duplicate paths")
        relative = Path(entry.path)
        _safe_snapshot_target(files_root, relative)
        _validate_non_negative_manifest_value(entry.size_bytes, "size_bytes")
        if len(entry.sha256) != 64 or any(
            char not in "0123456789abcdef" for char in entry.sha256
        ):
            raise ValueError("workspace snapshot manifest contains invalid sha256")
        manifest[entry.path] = entry

    actual_paths: set[str] = set()
    total_bytes = 0
    for path in sorted(files_root.rglob("*")):
        relative = path.relative_to(files_root)
        if path.is_symlink():
            raise ValueError(
                f"workspace snapshot does not support symlinks: {relative}"
            )
        _safe_snapshot_target(files_root, relative)
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(
                f"workspace snapshot only supports regular files: {relative}"
            )
        relative_posix = relative.as_posix()
        actual_paths.add(relative_posix)
        entry = manifest.get(relative_posix)
        if entry is None:
            raise ValueError("workspace snapshot manifest is missing file entry")
        size_bytes = path.stat().st_size
        total_bytes += size_bytes
        if size_bytes != entry.size_bytes:
            raise ValueError("workspace snapshot file size does not match manifest")
        if _sha256_file(path) != entry.sha256:
            raise ValueError("workspace snapshot file hash does not match manifest")
    if len(actual_paths) != snapshot.file_count:
        raise ValueError("workspace snapshot file count does not match manifest")
    if total_bytes != snapshot.total_bytes:
        raise ValueError("workspace snapshot byte count does not match manifest")
    if actual_paths != set(manifest):
        raise ValueError("workspace snapshot manifest paths do not match files")


def _copy_snapshot_files(files_root: Path, workspace_root: Path) -> None:
    for source in sorted(files_root.rglob("*")):
        relative = source.relative_to(files_root)
        target = workspace_root / relative
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copy2(source, target)


def _clear_workspace_except_preserved_roots(workspace_root: Path) -> None:
    for child in workspace_root.iterdir():
        if child.name in _PRESERVED_ROOT_NAMES:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
            continue
        child.unlink()


def _reject_nested_snapshot_and_workspace(
    snapshot: WorkspaceSnapshot,
    workspace_root: Path,
) -> None:
    snapshot_root = snapshot.snapshot_root.expanduser().resolve()
    files_root = snapshot.files_root.expanduser().resolve()
    for path, label in (
        (snapshot_root, "snapshot root"),
        (files_root, "snapshot files root"),
    ):
        try:
            _ = path.relative_to(workspace_root)
        except ValueError:
            pass
        else:
            raise ValueError(f"{label} must not be inside workspace root")
    try:
        _ = workspace_root.relative_to(snapshot_root)
    except ValueError:
        return
    raise ValueError("workspace root must not be inside snapshot root")


def _safe_snapshot_target(files_root: Path, relative: Path) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"workspace snapshot member escapes snapshot root: {relative}")
    if relative.parts and relative.parts[0] in _PRESERVED_ROOT_NAMES:
        raise ValueError(
            f"workspace snapshot must not contain preserved root entry: {relative}"
        )
    target = files_root / relative
    try:
        _ = target.relative_to(files_root)
    except ValueError as exc:
        raise ValueError(
            f"workspace snapshot member escapes snapshot root: {relative}"
        ) from exc
    return target


def _should_exclude_snapshot_path(relative: Path) -> bool:
    if not relative.parts:
        return True
    if relative.parts[0] in _PRESERVED_ROOT_NAMES:
        return True
    if any(part in _EXCLUDED_DIRECTORY_NAMES for part in relative.parts):
        return True
    return relative.name.endswith(_EXCLUDED_FILE_SUFFIXES)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            chunk = source.read(64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _validate_non_negative_manifest_value(value: int, field_name: str) -> None:
    if value < 0:
        raise ValueError(
            f"workspace snapshot manifest {field_name} must be non-negative"
        )
