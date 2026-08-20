"""Local Git workspace diff and patch helpers used by session workspace routes."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Literal


from coding_agent.environment import (
    WorkspaceDiff,
    WorkspaceDiffFile,
    WorkspacePatch,
)
from coding_agent.runs import (
    LocalPathWorkspaceRef,
)
from coding_agent.server.session_manager import Session

from coding_agent.server.http._bindings import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


def _session_local_workspace_root(session: Session) -> Path | None:
    target = session.default_run_target
    if target is None:
        raise RuntimeError("session is missing default_run_target")
    workspace = target.workspace
    if not isinstance(workspace, LocalPathWorkspaceRef):
        return None
    root = Path(workspace.path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"local workspace does not exist: {root}")
    if not (root / ".git").exists():
        raise ValueError("local workspace diff requires a Git workspace")
    return root


def _run_local_workspace_git(
    workspace_root: Path,
    args: list[str],
    operation: str,
    *,
    extra_env: Mapping[str, str] | None = None,
) -> str:
    git_binary = shutil.which("git")
    if git_binary is None:
        raise ValueError("git executable not found")
    try:
        result = subprocess.run(
            [git_binary, *args],
            cwd=workspace_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                **os.environ,
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
                **(extra_env or {}),
            },
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip()
        stdout = exc.stdout.strip()
        detail = stderr or stdout or f"exit code {exc.returncode}"
        raise ValueError(f"{operation} failed: {detail}") from exc
    return result.stdout


def _parse_local_git_name_status(
    output: str,
    numstat: Mapping[str, tuple[int | None, int | None, bool]],
) -> list[WorkspaceDiffFile]:
    tokens = [token for token in output.split("\0") if token]
    files: list[WorkspaceDiffFile] = []
    index = 0
    while index < len(tokens):
        status_and_path = tokens[index]
        index += 1
        status_parts = status_and_path.split("\t", 1)
        if len(status_parts) == 2:
            status_token, first_path = status_parts
        else:
            status_token = status_and_path
            if index >= len(tokens):
                raise ValueError("git diff name-status output is malformed")
            first_path = tokens[index]
            index += 1
        status_code = status_token[:1]
        old_path: str | None = None
        if status_code in {"R", "C"}:
            if index >= len(tokens):
                raise ValueError("git diff name-status output is malformed")
            old_path = first_path
            path = tokens[index]
            index += 1
            status: Literal[
                "added", "modified", "deleted", "renamed", "binary", "unknown"
            ] = "renamed"
        else:
            path = first_path
            status = _local_workspace_diff_status_from_git_status(status_code)

        additions, deletions, binary = numstat.get(path, (None, None, False))
        files.append(
            WorkspaceDiffFile(
                path=path,
                status=status,
                old_path=old_path,
                additions=additions,
                deletions=deletions,
                binary=binary,
            )
        )
    return files


def _local_workspace_diff_status_from_git_status(
    status_code: str,
) -> Literal["added", "modified", "deleted", "renamed", "binary", "unknown"]:
    if status_code == "A":
        return "added"
    if status_code == "M":
        return "modified"
    if status_code == "D":
        return "deleted"
    return "unknown"


def _parse_local_git_numstat(
    output: str,
) -> dict[str, tuple[int | None, int | None, bool]]:
    result: dict[str, tuple[int | None, int | None, bool]] = {}
    tokens = output.split("\0")
    index = 0
    while index < len(tokens):
        record = tokens[index]
        index += 1
        if not record:
            continue
        parts = record.split("\t", 2)
        if len(parts) != 3:
            raise ValueError("git diff numstat output is malformed")
        raw_additions, raw_deletions, raw_path = parts
        if raw_path:
            path = raw_path
        else:
            if index + 1 >= len(tokens):
                raise ValueError("git diff numstat output is malformed")
            _old_path = tokens[index]
            path = tokens[index + 1]
            index += 2
        if raw_additions == "-" or raw_deletions == "-":
            result[path] = (None, None, True)
            continue
        result[path] = (int(raw_additions), int(raw_deletions), False)
    return result


def _local_workspace_untracked_paths(workspace_root: Path) -> list[str]:
    output = _run_local_workspace_git(
        workspace_root,
        ["ls-files", "--others", "--exclude-standard", "-z"],
        "git ls-files --others",
    )
    return [path for path in output.split("\0") if path]


def _local_workspace_untracked_file(
    workspace_root: Path,
    relative_path: str,
) -> WorkspaceDiffFile:
    path = (workspace_root / relative_path).resolve()
    try:
        _ = path.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError(f"untracked path escapes workspace: {relative_path}") from exc
    if not path.is_file():
        return WorkspaceDiffFile(path=relative_path, status="unknown", binary=False)
    data = path.read_bytes()
    if b"\0" in data:
        return WorkspaceDiffFile(
            path=relative_path,
            status="added",
            additions=None,
            deletions=None,
            binary=True,
        )
    text = data.decode("utf-8", errors="replace")
    additions = 0 if text == "" else len(text.splitlines())
    return WorkspaceDiffFile(
        path=relative_path,
        status="added",
        additions=additions,
        deletions=0,
        binary=False,
    )


def _local_workspace_untracked_patch(workspace_root: Path, relative_path: str) -> str:
    path = (workspace_root / relative_path).resolve()
    try:
        _ = path.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError(f"untracked path escapes workspace: {relative_path}") from exc
    if not path.is_file():
        return ""
    data = path.read_bytes()
    if b"\0" in data:
        return f"diff --git a/{relative_path} b/{relative_path}\nnew file mode 100644\nBinary files /dev/null and b/{relative_path} differ\n"
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    patch_lines = [
        f"diff --git a/{relative_path} b/{relative_path}\n",
        "new file mode 100644\n",
        "index 0000000..0000000\n",
        "--- /dev/null\n",
        f"+++ b/{relative_path}\n",
        f"@@ -0,0 +1,{len(lines)} @@\n",
    ]
    patch_lines.extend(f"+{line}" for line in lines)
    if lines and not lines[-1].endswith("\n"):
        patch_lines.append("\n\\ No newline at end of file\n")
    return "".join(patch_lines)


def _local_workspace_diff(workspace_root: Path) -> WorkspaceDiff:
    name_status_output = _run_local_workspace_git(
        workspace_root,
        [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "--find-renames",
            "-z",
            "HEAD",
            "--",
        ],
        "git diff --name-status",
    )
    numstat_output = _run_local_workspace_git(
        workspace_root,
        [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--numstat",
            "--find-renames",
            "-z",
            "HEAD",
            "--",
        ],
        "git diff --numstat",
    )
    numstat = _parse_local_git_numstat(numstat_output)
    files = _parse_local_git_name_status(name_status_output, numstat)
    for untracked_path in _local_workspace_untracked_paths(workspace_root):
        file = _local_workspace_untracked_file(workspace_root, untracked_path)
        files.append(file)
        numstat[file.path] = (file.additions, file.deletions, file.binary)
    additions = sum(item[0] for item in numstat.values() if item[0] is not None)
    deletions = sum(item[1] for item in numstat.values() if item[1] is not None)
    return WorkspaceDiff(
        workspace_id=str(workspace_root),
        files=files,
        additions=additions,
        deletions=deletions,
    )


def _local_workspace_patch(workspace_root: Path) -> WorkspacePatch:
    patch = _run_local_workspace_git(
        workspace_root,
        [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--binary",
            "HEAD",
            "--",
        ],
        "git diff",
    )
    untracked_patch = "".join(
        _local_workspace_untracked_patch(workspace_root, path)
        for path in _local_workspace_untracked_paths(workspace_root)
    )
    return WorkspacePatch(
        workspace_id=str(workspace_root),
        format="unified_diff",
        patch=patch + untracked_patch,
    )


__all__ = [
    "_local_workspace_diff",
    "_local_workspace_diff_status_from_git_status",
    "_local_workspace_patch",
    "_local_workspace_untracked_file",
    "_local_workspace_untracked_patch",
    "_local_workspace_untracked_paths",
    "_parse_local_git_name_status",
    "_parse_local_git_numstat",
    "_run_local_workspace_git",
    "_session_local_workspace_root",
]
