from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


DEFAULT_MAX_EDIT_FILE_BYTES = 1024 * 1024


class SafeEditReason(StrEnum):
    OK = "ok"
    WORKSPACE_ESCAPE = "workspace_escape"
    SYMLINK = "symlink"
    MISSING_FILE = "missing_file"
    MISSING_PARENT = "missing_parent"
    NOT_FILE = "not_file"
    FILE_TOO_LARGE = "file_too_large"
    BINARY_FILE = "binary_file"


@dataclass(frozen=True)
class SafeEditDecision:
    allowed: bool
    reason: SafeEditReason
    normalized_path: str | None = None
    size_bytes: int | None = None
    max_size_bytes: int | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason.value,
            "normalized_path": self.normalized_path,
            "size_bytes": self.size_bytes,
            "max_size_bytes": self.max_size_bytes,
        }


def validate_safe_edit_path(
    workspace_root: Path | str,
    path: Path | str,
    *,
    allow_create: bool = False,
    max_file_bytes: int = DEFAULT_MAX_EDIT_FILE_BYTES,
) -> SafeEditDecision:
    root = Path(workspace_root).expanduser().resolve()
    raw_target = Path(path).expanduser()
    if not root.is_dir():
        raise ValueError(f"workspace root does not exist: {root}")

    if raw_target.is_absolute():
        target = raw_target
    else:
        target = root / raw_target

    existing_target = target.exists() or target.is_symlink()
    if existing_target:
        return _validate_existing_target(
            root=root,
            target=target,
            max_file_bytes=max_file_bytes,
        )
    return _validate_missing_target(
        root=root,
        target=target,
        allow_create=allow_create,
        max_file_bytes=max_file_bytes,
    )


def _validate_existing_target(
    *,
    root: Path,
    target: Path,
    max_file_bytes: int,
) -> SafeEditDecision:
    if target.is_symlink() or _has_symlink_parent(target, root):
        return _decision(False, SafeEditReason.SYMLINK, target, max_file_bytes)

    resolved = target.resolve()
    if not _is_relative_to(resolved, root):
        return _decision(
            False, SafeEditReason.WORKSPACE_ESCAPE, resolved, max_file_bytes
        )
    if not resolved.is_file():
        return _decision(False, SafeEditReason.NOT_FILE, resolved, max_file_bytes)

    size_bytes = resolved.stat().st_size
    if size_bytes > max_file_bytes:
        return _decision(
            False,
            SafeEditReason.FILE_TOO_LARGE,
            resolved,
            max_file_bytes,
            size_bytes=size_bytes,
        )
    if _looks_binary(resolved):
        return _decision(
            False,
            SafeEditReason.BINARY_FILE,
            resolved,
            max_file_bytes,
            size_bytes=size_bytes,
        )
    return _decision(
        True,
        SafeEditReason.OK,
        resolved,
        max_file_bytes,
        size_bytes=size_bytes,
    )


def _validate_missing_target(
    *,
    root: Path,
    target: Path,
    allow_create: bool,
    max_file_bytes: int,
) -> SafeEditDecision:
    parent = target.parent
    if parent.exists() or parent.is_symlink():
        if parent.is_symlink() or _has_symlink_parent(parent, root):
            return _decision(False, SafeEditReason.SYMLINK, target, max_file_bytes)
        resolved_parent = parent.resolve()
        if not _is_relative_to(resolved_parent, root):
            return _decision(
                False,
                SafeEditReason.WORKSPACE_ESCAPE,
                resolved_parent / target.name,
                max_file_bytes,
            )
        if not resolved_parent.is_dir():
            return _decision(
                False,
                SafeEditReason.NOT_FILE,
                resolved_parent / target.name,
                max_file_bytes,
            )
        normalized = resolved_parent / target.name
        if not allow_create:
            return _decision(
                False, SafeEditReason.MISSING_FILE, normalized, max_file_bytes
            )
        return _decision(True, SafeEditReason.OK, normalized, max_file_bytes)

    resolved_existing_parent = _nearest_existing_parent(parent)
    if resolved_existing_parent is None:
        return _decision(False, SafeEditReason.MISSING_PARENT, target, max_file_bytes)
    if resolved_existing_parent.is_symlink() or _has_symlink_parent(
        resolved_existing_parent, root
    ):
        return _decision(False, SafeEditReason.SYMLINK, target, max_file_bytes)
    resolved_parent = resolved_existing_parent.resolve()
    if not _is_relative_to(resolved_parent, root):
        return _decision(False, SafeEditReason.WORKSPACE_ESCAPE, target, max_file_bytes)
    if not allow_create:
        return _decision(False, SafeEditReason.MISSING_FILE, target, max_file_bytes)
    return _decision(False, SafeEditReason.MISSING_PARENT, target, max_file_bytes)


def _decision(
    allowed: bool,
    reason: SafeEditReason,
    path: Path,
    max_size_bytes: int,
    *,
    size_bytes: int | None = None,
) -> SafeEditDecision:
    return SafeEditDecision(
        allowed=allowed,
        reason=reason,
        normalized_path=str(path),
        size_bytes=size_bytes,
        max_size_bytes=max_size_bytes,
    )


def _has_symlink_parent(path: Path, root: Path) -> bool:
    current = path.parent
    root_resolved = root.resolve()
    while True:
        if current.exists() and current.is_symlink():
            return True
        if current == root_resolved or current.parent == current:
            return False
        current = current.parent


def _nearest_existing_parent(path: Path) -> Path | None:
    current = path
    while not current.exists():
        if current.parent == current:
            return None
        current = current.parent
    return current


def _looks_binary(path: Path) -> bool:
    return b"\0" in path.read_bytes()[:8192]


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
