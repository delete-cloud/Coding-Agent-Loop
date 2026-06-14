from __future__ import annotations

import base64
import gzip
import io
import os
import shutil
import tarfile
import tempfile
import zlib
from pathlib import Path
from typing import Protocol, override


_PRESERVED_ROOT_NAMES = frozenset({".git"})
_EXCLUDED_DIRECTORY_NAMES = frozenset({"__pycache__"})
_EXCLUDED_FILE_SUFFIXES = (".pyc", ".pyo")
_MAX_WORKSPACE_ARCHIVE_BYTES = 8 * 1024 * 1024
_MAX_WORKSPACE_ARCHIVE_BASE64_CHARS = 4 * ((_MAX_WORKSPACE_ARCHIVE_BYTES + 2) // 3)
_MAX_WORKSPACE_TAR_STREAM_BYTES = 16 * 1024 * 1024
_MAX_WORKSPACE_ARCHIVE_MEMBERS = 4096


def create_workspace_archive_base64(workspace_root: Path) -> str:
    root = workspace_root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"workspace root does not exist: {root}")

    buffer = io.BytesIO()
    total_input_bytes = 0
    member_count = 0
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if should_exclude_workspace_archive_path(relative):
                continue
            if path.is_symlink():
                raise ValueError(
                    f"workspace archive does not support symlinks: {relative}"
                )
            if not path.is_file():
                continue
            member_count += 1
            if member_count > _MAX_WORKSPACE_ARCHIVE_MEMBERS:
                raise ValueError("workspace archive contains too many members")
            stat_result = path.stat()
            total_input_bytes += stat_result.st_size
            _raise_if_archive_too_large(total_input_bytes)
            data = path.read_bytes()
            info = tarfile.TarInfo(name=relative.as_posix())
            info.size = len(data)
            info.mode = stat_result.st_mode & 0o777
            info.mtime = int(stat_result.st_mtime)
            archive.addfile(info, io.BytesIO(data))
    archive_bytes = buffer.getvalue()
    _raise_if_archive_too_large(len(archive_bytes))
    return base64.b64encode(archive_bytes).decode("ascii")


def should_exclude_workspace_archive_path(relative: Path) -> bool:
    if not relative.parts:
        return True
    if relative.parts[0] in _PRESERVED_ROOT_NAMES:
        return True
    if any(part in _EXCLUDED_DIRECTORY_NAMES for part in relative.parts):
        return True
    return relative.name.endswith(_EXCLUDED_FILE_SUFFIXES)


def extract_workspace_archive_base64(workspace_root: Path, archive_base64: str) -> None:
    root = workspace_root.expanduser().resolve()
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise ValueError(f"workspace root is not a directory: {root}")

    _raise_if_archive_base64_too_large(archive_base64)
    try:
        archive_bytes = base64.b64decode(archive_base64.encode("ascii"), validate=True)
    except ValueError as exc:
        raise ValueError("workspace archive must be valid base64") from exc
    _raise_if_archive_too_large(len(archive_bytes))

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir).resolve()
        try:
            _extract_workspace_archive_to_temp(temp_root, archive_bytes)
        except (tarfile.TarError, gzip.BadGzipFile, EOFError, zlib.error) as exc:
            raise ValueError("workspace archive is not a valid tar.gz") from exc

        _clear_extract_target(root)
        _copy_extracted_workspace(temp_root, root)


def _extract_workspace_archive_to_temp(temp_root: Path, archive_bytes: bytes) -> None:
    archive_buffer = io.BytesIO(archive_bytes)
    gzip_stream = gzip.GzipFile(fileobj=archive_buffer, mode="rb")
    counted_stream = _CountingReader(
        gzip_stream,
        max_bytes=_MAX_WORKSPACE_TAR_STREAM_BYTES,
    )
    extracted_bytes = 0
    member_count = 0
    with tarfile.open(fileobj=counted_stream, mode="r|") as archive:
        for member in archive:
            member_count += 1
            if member_count > _MAX_WORKSPACE_ARCHIVE_MEMBERS:
                raise ValueError("workspace archive contains too many members")
            _reject_preserved_root_member(member.name)
            target_path = _safe_archive_target(temp_root, member.name)
            if member.isdir():
                target_path.mkdir(parents=True, exist_ok=True)
                target_path.chmod(_member_mode(member))
                continue
            if not member.isfile():
                raise ValueError(
                    f"workspace archive only supports regular files and directories: {member.name}"
                )
            if member.size < 0:
                raise ValueError("workspace archive is not a valid tar.gz")
            extracted_bytes += member.size
            _raise_if_archive_too_large(extracted_bytes)
            file_obj = archive.extractfile(member)
            if file_obj is None:
                raise ValueError(
                    f"workspace archive member is unreadable: {member.name}"
                )
            _copy_archive_file(file_obj, target_path, member)


def _copy_archive_file(
    file_obj: _ReadableBinary, target_path: Path, member: tarfile.TarInfo
) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    copied_bytes = 0
    with target_path.open("wb") as target_file:
        while True:
            chunk = file_obj.read(64 * 1024)
            if not chunk:
                break
            copied_bytes += len(chunk)
            if copied_bytes > member.size:
                raise ValueError("workspace archive is not a valid tar.gz")
            _ = target_file.write(chunk)
    if copied_bytes != member.size:
        raise ValueError("workspace archive is not a valid tar.gz")
    target_path.chmod(_member_mode(member))
    os.utime(target_path, (member.mtime, member.mtime))


def _copy_extracted_workspace(temp_root: Path, workspace_root: Path) -> None:
    for source_path in sorted(temp_root.rglob("*")):
        relative = source_path.relative_to(temp_root)
        target_path = workspace_root / relative
        if source_path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
            target_path.chmod(source_path.stat().st_mode & 0o777)
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copy2(source_path, target_path)


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
        raise ValueError(
            f"workspace archive member escapes workspace root: {member_name}"
        )
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


def _reject_preserved_root_member(member_name: str) -> None:
    member_path = Path(member_name)
    if member_path.parts and member_path.parts[0] in _PRESERVED_ROOT_NAMES:
        raise ValueError(
            f"workspace archive must not contain preserved root entry: {member_name}"
        )


def _raise_if_archive_too_large(size_bytes: int) -> None:
    if size_bytes > _MAX_WORKSPACE_ARCHIVE_BYTES:
        raise ValueError("workspace archive exceeds 8 MiB limit")


def _raise_if_archive_base64_too_large(archive_base64: str) -> None:
    if len(archive_base64) > _MAX_WORKSPACE_ARCHIVE_BASE64_CHARS:
        raise ValueError("workspace archive exceeds 8 MiB limit")


class _ReadableBinary(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


class _CountingReader(io.BufferedIOBase):
    def __init__(self, stream: _ReadableBinary, *, max_bytes: int) -> None:
        self._stream: _ReadableBinary = stream
        self._max_bytes: int = max_bytes
        self._bytes_read: int = 0

    @override
    def readable(self) -> bool:
        return True

    @override
    def read(self, size: int | None = -1) -> bytes:
        if size is None:
            size = -1
        chunk = self._stream.read(size)
        self._bytes_read += len(chunk)
        if self._bytes_read > self._max_bytes:
            raise ValueError("workspace archive exceeds 8 MiB limit")
        return chunk
