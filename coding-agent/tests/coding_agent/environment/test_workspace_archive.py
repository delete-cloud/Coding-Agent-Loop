from __future__ import annotations

import base64
import io
import os
import tarfile
import time
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
    script = source / "nested" / "run.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    timestamp = int(time.time()) - 120
    os.utime(script, (timestamp, timestamp))

    archive_base64 = create_workspace_archive_base64(source)

    target = tmp_path / "target"
    extract_workspace_archive_base64(target, archive_base64)

    assert (target / "README.md").read_text(encoding="utf-8") == "hello\n"
    assert (target / "nested" / "data.txt").read_text(encoding="utf-8") == "cloud\n"
    extracted_script = target / "nested" / "run.sh"
    assert extracted_script.read_text(encoding="utf-8") == "#!/bin/sh\nexit 0\n"
    assert extracted_script.stat().st_mode & 0o777 == 0o755
    assert int(extracted_script.stat().st_mtime) == timestamp


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


def test_workspace_archive_rejects_invalid_tar_without_clearing_target(tmp_path: Path) -> None:
    target = tmp_path / "repo"
    target.mkdir()
    (target / "keep.txt").write_text("keep\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"valid tar\.gz"):
        _ = extract_workspace_archive_base64(
            target,
            base64.b64encode(b"not a valid tar archive").decode("ascii"),
        )

    assert (target / "keep.txt").read_text(encoding="utf-8") == "keep\n"


def test_workspace_archive_rejects_tar_header_error_without_clearing_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "repo"
    target.mkdir()
    (target / "keep.txt").write_text("keep\n", encoding="utf-8")

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        data = b"hello\n"
        info = tarfile.TarInfo(name="plain.txt")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))

    archive_buffer = io.BytesIO(buffer.getvalue())
    with tarfile.open(fileobj=archive_buffer, mode="r:gz") as archive:
        members = archive.getmembers()
        assert len(members) == 1
        header_offset = members[0].offset_data - 512

    archive_bytes = bytearray(buffer.getvalue())
    archive_bytes[header_offset] = 0

    with pytest.raises(ValueError, match=r"valid tar\.gz"):
        _ = extract_workspace_archive_base64(
            target,
            base64.b64encode(bytes(archive_bytes)).decode("ascii"),
        )

    assert (target / "keep.txt").read_text(encoding="utf-8") == "keep\n"


def test_workspace_archive_rejects_symlink_without_clearing_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "repo"
    target.mkdir()
    (target / "keep.txt").write_text("keep\n", encoding="utf-8")

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        link = tarfile.TarInfo(name="link")
        link.type = tarfile.SYMTYPE
        link.linkname = "keep.txt"
        archive.addfile(link)

    with pytest.raises(ValueError, match="only supports regular files and directories"):
        _ = extract_workspace_archive_base64(
            target,
            base64.b64encode(buffer.getvalue()).decode("ascii"),
        )

    assert (target / "keep.txt").read_text(encoding="utf-8") == "keep\n"


def test_workspace_archive_defaults_missing_member_mode_to_readable_file(
    tmp_path: Path,
) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        data = b"hello\n"
        info = tarfile.TarInfo(name="plain.txt")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))

    target = tmp_path / "target"
    extract_workspace_archive_base64(
        target,
        base64.b64encode(buffer.getvalue()).decode("ascii"),
    )

    extracted = target / "plain.txt"
    assert extracted.read_text(encoding="utf-8") == "hello\n"
    assert extracted.stat().st_mode & 0o777 == 0o644


def test_workspace_archive_extract_reconciles_deletions_but_preserves_git(tmp_path: Path) -> None:
    target = tmp_path / "repo"
    (target / ".git").mkdir(parents=True)
    (target / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (target / "deleted.txt").write_text("stale\n", encoding="utf-8")
    source = tmp_path / "source"
    source.mkdir()
    (source / "kept.txt").write_text("fresh\n", encoding="utf-8")

    extract_workspace_archive_base64(target, create_workspace_archive_base64(source))

    assert not (target / "deleted.txt").exists()
    assert (target / "kept.txt").read_text(encoding="utf-8") == "fresh\n"
    assert (target / ".git" / "HEAD").read_text(encoding="utf-8") == "ref: refs/heads/main\n"


def test_workspace_archive_create_rejects_input_larger_than_limit(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "large.bin").write_bytes(b"a" * (8 * 1024 * 1024 + 1))

    with pytest.raises(ValueError, match="exceeds 8 MiB limit"):
        _ = create_workspace_archive_base64(source)


def test_workspace_archive_create_rejects_oversized_file_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    oversized = source / "large.bin"
    oversized.write_bytes(b"a" * (8 * 1024 * 1024 + 1))

    def fail_if_read(path: Path) -> bytes:
        if path == oversized:
            raise AssertionError("oversized file should be rejected before read")
        return original_read_bytes(path)

    original_read_bytes = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", fail_if_read)

    with pytest.raises(ValueError, match="exceeds 8 MiB limit"):
        _ = create_workspace_archive_base64(source)


def test_workspace_archive_extract_rejects_decoded_archive_larger_than_limit(
    tmp_path: Path,
) -> None:
    archive_base64 = base64.b64encode(b"a" * (8 * 1024 * 1024 + 1)).decode("ascii")

    with pytest.raises(ValueError, match="exceeds 8 MiB limit"):
        _ = extract_workspace_archive_base64(tmp_path / "target", archive_base64)


def test_workspace_archive_extract_rejects_member_payload_larger_than_limit(
    tmp_path: Path,
) -> None:
    buffer = io.BytesIO()
    data = b"a" * (8 * 1024 * 1024 + 1)
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo(name="large.bin")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))

    with pytest.raises(ValueError, match="exceeds 8 MiB limit"):
        _ = extract_workspace_archive_base64(
            tmp_path / "target",
            base64.b64encode(buffer.getvalue()).decode("ascii"),
        )
