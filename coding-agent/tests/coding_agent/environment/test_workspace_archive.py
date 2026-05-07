from __future__ import annotations

import base64
import io
import tarfile
from pathlib import Path

import pytest

from coding_agent.workspace_archive import (
    create_workspace_archive_base64,
    extract_workspace_archive_base64,
)


def test_workspace_archive_round_trips_nested_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    (source / "README.md").write_text("hello\n", encoding="utf-8")
    (source / "nested" / "data.txt").write_text("cloud\n", encoding="utf-8")

    archive_base64 = create_workspace_archive_base64(source)

    target = tmp_path / "target"
    extract_workspace_archive_base64(target, archive_base64)

    assert (target / "README.md").read_text(encoding="utf-8") == "hello\n"
    assert (target / "nested" / "data.txt").read_text(encoding="utf-8") == "cloud\n"


def test_workspace_archive_rejects_path_traversal_members(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        data = b"outside\n"
        info = tarfile.TarInfo(name="../escape.txt")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))

    with pytest.raises(ValueError, match="escapes workspace root"):
        _ = extract_workspace_archive_base64(
            tmp_path / "target",
            base64.b64encode(buffer.getvalue()).decode("ascii"),
        )
