from __future__ import annotations

import base64
import io
import os
import shutil
import tarfile
from pathlib import Path


_PRESERVED_ROOT_NAMES = frozenset({".git"})


def create_workspace_archive_base64(workspace_root: Path) -> str:
    root = workspace_root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"workspace root does not exist: {root}")

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if not relative.parts or relative.parts[0] == ".git":
                continue
            if path.is_symlink():
                raise ValueError(f"workspace archive does not support symlinks: {relative}")
            if not path.is_file():
                continue
            stat_result = path.stat()
            data = path.read_bytes()
            info = tarfile.TarInfo(name=relative.as_posix())
            info.size = len(data)
            info.mode = stat_result.st_mode & 0o777
            info.mtime = int(stat_result.st_mtime)
            archive.addfile(info, io.BytesIO(data))
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def extract_workspace_archive_base64(workspace_root: Path, archive_base64: str) -> None:
    root = workspace_root.expanduser().resolve()
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise ValueError(f"workspace root is not a directory: {root}")

    try:
        archive_bytes = base64.b64decode(archive_base64.encode("ascii"), validate=True)
    except ValueError as exc:
        raise ValueError("workspace archive must be valid base64") from exc

    archive_buffer = io.BytesIO(archive_bytes)
    try:
        with tarfile.open(fileobj=archive_buffer, mode="r:gz") as archive:
            for member in archive.getmembers():
                _safe_archive_target(root, member.name)
    except tarfile.ReadError as exc:
        raise ValueError("workspace archive is not a valid tar.gz") from exc

    _clear_extract_target(root)
    archive_buffer.seek(0)

    with tarfile.open(fileobj=archive_buffer, mode="r:gz") as archive:
        for member in archive.getmembers():
            target_path = _safe_archive_target(root, member.name)
            if member.isdir():
                target_path.mkdir(parents=True, exist_ok=True)
                target_path.chmod(_member_mode(member))
                continue
            if not member.isfile():
                raise ValueError(
                    f"workspace archive only supports regular files and directories: {member.name}"
                )
            file_obj = archive.extractfile(member)
            if file_obj is None:
                raise ValueError(f"workspace archive member is unreadable: {member.name}")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            _ = target_path.write_bytes(file_obj.read())
            target_path.chmod(_member_mode(member))
            os.utime(target_path, (member.mtime, member.mtime))


def _clear_extract_target(workspace_root: Path) -> None:
    for child in workspace_root.iterdir():
        if child.name in _PRESERVED_ROOT_NAMES:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
            continue
        child.unlink()


def _member_mode(member: tarfile.TarInfo) -> int:
    mode = member.mode & 0o777
    if mode != 0:
        return mode
    if member.isdir():
        return 0o755
    return 0o644


def _safe_archive_target(workspace_root: Path, member_name: str) -> Path:
    member_path = Path(member_name)
    if member_path.is_absolute() or ".." in member_path.parts:
        raise ValueError(f"workspace archive member escapes workspace root: {member_name}")
    if not member_path.parts:
        raise ValueError("workspace archive member name must be non-empty")
    candidate = (workspace_root / member_path).resolve()
    try:
        _ = candidate.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError(
            f"workspace archive member escapes workspace root: {member_name}"
        ) from exc
    return candidate
