from __future__ import annotations

import json
import textwrap

import pytest

from coding_agent.action_safety.patch_plan import (
    PatchOperation,
    PatchRiskLevel,
    build_patch_plan,
)


def test_patch_plan_summarizes_hunks_and_risk_without_file_content() -> None:
    patch = textwrap.dedent(
        """\
        @@ -1,3 +1,3 @@
         def token():
        -    return "SECRET_VALUE"
        +    return "public"
         done = True
        """
    )

    plan = build_patch_plan("src/app.py", patch, file_exists=True)
    payload = plan.to_safe_dict()
    serialized = json.dumps(payload, sort_keys=True)

    assert payload == {
        "path": "src/app.py",
        "operation": "modify",
        "file_exists": True,
        "hunk_count": 1,
        "additions": 1,
        "deletions": 1,
        "risk_level": "low",
        "risk_reasons": ["small_modify"],
        "hunks": [
            {
                "index": 0,
                "old_start": 1,
                "old_count": 3,
                "new_start": 1,
                "new_count": 3,
                "additions": 1,
                "deletions": 1,
                "context_lines": 2,
            }
        ],
    }
    assert "SECRET_VALUE" not in serialized
    assert "public" not in serialized


def test_patch_plan_classifies_create_delete_and_larger_changes() -> None:
    create_patch = textwrap.dedent(
        """\
        @@ -0,0 +1,2 @@
        +first
        +second
        """
    )
    delete_patch = textwrap.dedent(
        """\
        @@ -1,2 +0,0 @@
        -first
        -second
        """
    )
    larger_patch = "\n".join(
        ["@@ -1,1 +1,31 @@", "-old", *(f"+line {index}" for index in range(31))]
    )

    create_plan = build_patch_plan("created.txt", create_patch, file_exists=False)
    delete_plan = build_patch_plan("deleted.txt", delete_patch, file_exists=True)
    larger_plan = build_patch_plan("large.txt", larger_patch, file_exists=True)

    assert create_plan.operation == PatchOperation.CREATE
    assert create_plan.risk_level == PatchRiskLevel.MEDIUM
    assert create_plan.risk_reasons == ("operation_create",)
    assert delete_plan.operation == PatchOperation.DELETE
    assert delete_plan.risk_level == PatchRiskLevel.HIGH
    assert delete_plan.risk_reasons == ("operation_delete",)
    assert larger_plan.operation == PatchOperation.MODIFY
    assert larger_plan.risk_level == PatchRiskLevel.MEDIUM
    assert larger_plan.risk_reasons == ("moderate_change",)


def test_patch_plan_tracks_multiple_hunks() -> None:
    patch = textwrap.dedent(
        """\
        @@ -1,1 +1,1 @@
        -old one
        +new one
        @@ -10,1 +10,1 @@
        -old two
        +new two
        """
    )

    plan = build_patch_plan("src/app.py", patch, file_exists=True)

    assert plan.hunk_count == 2
    assert plan.additions == 2
    assert plan.deletions == 2
    assert plan.risk_level == PatchRiskLevel.MEDIUM
    assert plan.risk_reasons == ("multiple_hunks",)
    assert [hunk.index for hunk in plan.hunks] == [0, 1]


def test_patch_plan_rejects_patch_without_hunks() -> None:
    with pytest.raises(ValueError, match="No hunks found"):
        build_patch_plan("src/app.py", "not a unified diff", file_exists=True)


def test_patch_plan_rejects_multi_file_patch() -> None:
    patch = textwrap.dedent(
        """\
        --- a/one.txt
        +++ b/one.txt
        @@ -1,1 +1,1 @@
        -old one
        +new one
        --- a/two.txt
        +++ b/two.txt
        @@ -1,1 +1,1 @@
        -old two
        +new two
        """
    )

    with pytest.raises(ValueError, match="single target file"):
        build_patch_plan("one.txt", patch, file_exists=True)


def test_patch_plan_accepts_single_file_git_diff_headers() -> None:
    patch = textwrap.dedent(
        """\
        diff --git a/one.txt b/one.txt
        index 1111111..2222222 100644
        --- a/one.txt
        +++ b/one.txt
        @@ -1,1 +1,1 @@
        -old one
        +new one
        """
    )

    plan = build_patch_plan("one.txt", patch, file_exists=True)

    assert plan.hunk_count == 1
    assert plan.additions == 1
    assert plan.deletions == 1


def test_patch_plan_rejects_hunk_header_count_mismatch() -> None:
    patch = textwrap.dedent(
        """\
        @@ -1,200 +1,200 @@
        -old
        +new
        """
    )

    with pytest.raises(ValueError, match="Hunk body does not match header counts"):
        build_patch_plan("src/app.py", patch, file_exists=True)
