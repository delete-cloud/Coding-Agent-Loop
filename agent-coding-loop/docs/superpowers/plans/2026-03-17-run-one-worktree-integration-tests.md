# run_one Worktree Integration Tests — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add integration tests that exercise real git worktree creation, overlay materialization, and cleanup in `run_one()`.

**Architecture:** New test file with a selective `subprocess.run` proxy (intercepts agent-loop, passes git commands through) and a real git repo fixture. Five tests cover: normal lifecycle, overlay copy, timeout cleanup, prepare failure, rate limiter release.

**Tech Stack:** Python 3, unittest, subprocess, tempfile, git CLI

**Spec:** `docs/superpowers/specs/2026-03-17-run-one-worktree-integration-tests-design.md`

---

## Chunk 1: Helpers and all 5 tests

### Task 1: Scaffold file with helpers

**Files:**
- Create: `eval/tests/test_run_one_integration.py`

- [ ] **Step 1: Create the test file with imports and both helpers**

```python
"""Integration tests for run_one() with real git worktree isolation."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from eval.ab.run_ab import (
    _RateLimiter,
    run_one,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_real_subprocess_run = subprocess.run


@contextmanager
def selective_subprocess_mock(
    agent_loop_bin,
    *,
    result=None,
    side_effect=None,
    on_agent_call=None,
):
    """Patch subprocess.run so that only agent-loop invocations are faked.

    All other commands (git, etc.) are delegated to the real subprocess.run.
    Yields a list that collects (cmd, kwargs) tuples for every intercepted
    agent-loop call.
    """
    calls = []

    def _proxy(cmd, **kwargs):
        if isinstance(cmd, (list, tuple)) and len(cmd) > 0 and cmd[0] == agent_loop_bin:
            calls.append((list(cmd), kwargs))
            if on_agent_call is not None:
                on_agent_call(list(cmd), **kwargs)
            if side_effect is not None:
                raise side_effect
            if result is not None:
                return result
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        return _real_subprocess_run(cmd, **kwargs)

    with mock.patch("eval.ab.run_ab.subprocess.run", side_effect=_proxy):
        yield calls


def create_test_git_repo():
    """Create a minimal git repo with an initial commit.

    Returns (repo_path, cleanup_fn).  The repo has a single committed
    README.md.  Overlay source files are NOT committed so that worktree
    tests can verify materialize_overlay actually copies them.
    """
    root = tempfile.mkdtemp(prefix="test_repo_")
    try:
        subprocess.run(["git", "init", root], capture_output=True, check=True)
        subprocess.run(
            ["git", "-C", root, "config", "user.email", "test@example.com"],
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "-C", root, "config", "user.name", "tester"],
            capture_output=True,
            check=True,
        )
        readme = Path(root) / "README.md"
        readme.write_text("test repo\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", root, "add", "README.md"],
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "-C", root, "commit", "-m", "init"],
            capture_output=True,
            check=True,
        )
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise

    def cleanup():
        # Prune any leftover worktrees before removing the directory tree.
        subprocess.run(
            ["git", "-C", root, "worktree", "prune"],
            capture_output=True,
            check=False,
        )
        shutil.rmtree(root, ignore_errors=True)

    return root, cleanup
```

- [ ] **Step 2: Verify the file is importable**

Run (from repo root): `python3 -c "from eval.tests.test_run_one_integration import selective_subprocess_mock, create_test_git_repo; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add eval/tests/test_run_one_integration.py
git commit -m "test: scaffold run_one integration test helpers"
```

---

### Task 2: Test — isolate worktree creates and cleans up

**Files:**
- Modify: `eval/tests/test_run_one_integration.py`

- [ ] **Step 1: Write the test**

Append to the file:

```python
class RunOneWorktreeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.repo, self._cleanup = create_test_git_repo()
        self.db_path = os.path.join(tempfile.mkdtemp(prefix="test_db_"), "state.db")

    def tearDown(self):
        self._cleanup()
        db_dir = os.path.dirname(self.db_path)
        if os.path.isdir(db_dir):
            shutil.rmtree(db_dir, ignore_errors=True)

    def test_isolate_worktree_creates_and_cleans_up(self):
        agent_bin = "./agent-loop"
        worktree_repo_path = None

        def capture_repo(cmd, **kwargs):
            nonlocal worktree_repo_path
            for i, arg in enumerate(cmd):
                if arg == "--repo" and i + 1 < len(cmd):
                    worktree_repo_path = cmd[i + 1]
                    break
            # Verify the worktree directory exists at call time.
            self.assertIsNotNone(worktree_repo_path)
            self.assertTrue(
                os.path.isdir(worktree_repo_path),
                f"worktree repo should exist during agent call: {worktree_repo_path}",
            )

        fake_result = SimpleNamespace(
            stdout=json.dumps({"status": "completed", "summary": "ok", "run_id": ""}),
            stderr="",
            returncode=0,
        )
        with selective_subprocess_mock(
            agent_bin, result=fake_result, on_agent_call=capture_repo
        ) as calls:
            row = run_one(
                experiment="no_rag",
                rag_enabled=False,
                task={"task_id": "t1", "goal": "fix readme", "requires_kb": False},
                agent_loop_bin=agent_bin,
                repo=self.repo,
                db_path=self.db_path,
                pr_mode="dry-run",
                max_iterations=1,
                kb_url="http://127.0.0.1:0",
                dry_run=False,
                task_timeout_sec=60,
                strict_mode=False,
                isolate_worktree=True,
            )

        self.assertEqual(row["status"], "completed")
        self.assertEqual(len(calls), 1)
        # After run_one returns, the worktree directory should be cleaned up.
        self.assertIsNotNone(worktree_repo_path)
        self.assertFalse(
            os.path.exists(worktree_repo_path),
            f"worktree repo should be cleaned up after run_one: {worktree_repo_path}",
        )
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python3 -m unittest eval.tests.test_run_one_integration.RunOneWorktreeIntegrationTests.test_isolate_worktree_creates_and_cleans_up -v`

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add eval/tests/test_run_one_integration.py
git commit -m "test: run_one isolate worktree creates and cleans up"
```

---

### Task 3: Test — isolate worktree materializes overlay

**Files:**
- Modify: `eval/tests/test_run_one_integration.py`

- [ ] **Step 1: Write the test**

Add to `RunOneWorktreeIntegrationTests`:

```python
    def test_isolate_worktree_materializes_overlay(self):
        agent_bin = "./agent-loop"
        overlay_content = "# Test KB Doc\nThis is overlay content.\n"

        # Create overlay file in base repo AFTER initial commit.
        # The worktree (detached at HEAD) won't have this file unless
        # materialize_overlay copies it.
        overlay_src = Path(self.repo) / "eval" / "ab" / "kb" / "test_doc.md"
        overlay_src.parent.mkdir(parents=True, exist_ok=True)
        overlay_src.write_text(overlay_content, encoding="utf-8")

        found_in_worktree = {}

        def check_overlay(cmd, **kwargs):
            for i, arg in enumerate(cmd):
                if arg == "--repo" and i + 1 < len(cmd):
                    wt_repo = cmd[i + 1]
                    overlay_dst = Path(wt_repo) / "eval" / "ab" / "kb" / "test_doc.md"
                    found_in_worktree["exists"] = overlay_dst.exists()
                    if overlay_dst.exists():
                        found_in_worktree["content"] = overlay_dst.read_text(encoding="utf-8")
                    break

        fake_result = SimpleNamespace(
            stdout=json.dumps({"status": "completed", "summary": "ok", "run_id": ""}),
            stderr="",
            returncode=0,
        )
        with selective_subprocess_mock(
            agent_bin, result=fake_result, on_agent_call=check_overlay
        ):
            run_one(
                experiment="rag",
                rag_enabled=True,
                task={
                    "task_id": "kb_001",
                    "goal": "check eval/ab/kb/test_doc.md",
                    "requires_kb": True,
                    "expected_citations": ["eval/ab/kb/test_doc.md"],
                },
                agent_loop_bin=agent_bin,
                repo=self.repo,
                db_path=self.db_path,
                pr_mode="dry-run",
                max_iterations=1,
                kb_url="http://127.0.0.1:0",
                dry_run=False,
                task_timeout_sec=60,
                strict_mode=False,
                isolate_worktree=True,
            )

        self.assertTrue(
            found_in_worktree.get("exists", False),
            "overlay file should exist in worktree after materialize_overlay",
        )
        self.assertEqual(
            found_in_worktree.get("content"),
            overlay_content,
            "overlay file content should match base repo source",
        )
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python3 -m unittest eval.tests.test_run_one_integration.RunOneWorktreeIntegrationTests.test_isolate_worktree_materializes_overlay -v`

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add eval/tests/test_run_one_integration.py
git commit -m "test: run_one isolate worktree materializes overlay"
```

---

### Task 4: Test — isolate worktree cleans up on timeout

**Files:**
- Modify: `eval/tests/test_run_one_integration.py`

- [ ] **Step 1: Write the test**

Add to `RunOneWorktreeIntegrationTests`:

```python
    def test_isolate_worktree_cleans_up_on_timeout(self):
        agent_bin = "./agent-loop"
        worktree_repo_path = None

        def capture_repo(cmd, **kwargs):
            nonlocal worktree_repo_path
            for i, arg in enumerate(cmd):
                if arg == "--repo" and i + 1 < len(cmd):
                    worktree_repo_path = cmd[i + 1]
                    break

        timeout_exc = subprocess.TimeoutExpired(
            cmd=[agent_bin], timeout=1, output="", stderr=""
        )
        with selective_subprocess_mock(
            agent_bin, side_effect=timeout_exc, on_agent_call=capture_repo
        ):
            row = run_one(
                experiment="no_rag",
                rag_enabled=False,
                task={"task_id": "t2", "goal": "fix readme", "requires_kb": False},
                agent_loop_bin=agent_bin,
                repo=self.repo,
                db_path=self.db_path,
                pr_mode="dry-run",
                max_iterations=1,
                kb_url="http://127.0.0.1:0",
                dry_run=False,
                task_timeout_sec=60,
                strict_mode=False,
                isolate_worktree=True,
            )

        self.assertTrue(row.get("timed_out", False))
        self.assertEqual(row["status"], "failed")
        self.assertIsNotNone(worktree_repo_path)
        self.assertFalse(
            os.path.exists(worktree_repo_path),
            f"worktree repo should be cleaned up after timeout: {worktree_repo_path}",
        )
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python3 -m unittest eval.tests.test_run_one_integration.RunOneWorktreeIntegrationTests.test_isolate_worktree_cleans_up_on_timeout -v`

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add eval/tests/test_run_one_integration.py
git commit -m "test: run_one isolate worktree cleans up on timeout"
```

---

### Task 5: Test — fails gracefully on prepare error

**Files:**
- Modify: `eval/tests/test_run_one_integration.py`

- [ ] **Step 1: Write the test**

Add to `RunOneWorktreeIntegrationTests`:

```python
    def test_isolate_worktree_fails_gracefully_on_prepare_error(self):
        with mock.patch(
            "eval.ab.run_ab.prepare_isolated_repo",
            side_effect=RuntimeError("disk full"),
        ):
            row = run_one(
                experiment="no_rag",
                rag_enabled=False,
                task={"task_id": "t3", "goal": "fix readme", "requires_kb": False},
                agent_loop_bin="./agent-loop",
                repo="/tmp/nonexistent",
                db_path="/tmp/nonexistent.db",
                pr_mode="dry-run",
                max_iterations=1,
                kb_url="http://127.0.0.1:0",
                dry_run=False,
                task_timeout_sec=60,
                strict_mode=False,
                isolate_worktree=True,
            )

        self.assertEqual(row["status"], "failed")
        self.assertIn("isolate worktree failed", row["summary"])
        self.assertIn("disk full", row["stderr_preview"])
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python3 -m unittest eval.tests.test_run_one_integration.RunOneWorktreeIntegrationTests.test_isolate_worktree_fails_gracefully_on_prepare_error -v`

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add eval/tests/test_run_one_integration.py
git commit -m "test: run_one fails gracefully on prepare error"
```

---

### Task 6: Test — rate limiter released on timeout

**Files:**
- Modify: `eval/tests/test_run_one_integration.py`

- [ ] **Step 1: Write the test**

Add to `RunOneWorktreeIntegrationTests`:

```python
    def test_rate_limiter_released_on_timeout(self):
        agent_bin = "./agent-loop"
        limiter = _RateLimiter(max_concurrent=1, min_interval_sec=0.0)

        timeout_exc = subprocess.TimeoutExpired(
            cmd=[agent_bin], timeout=1, output="", stderr=""
        )
        with selective_subprocess_mock(agent_bin, side_effect=timeout_exc):
            run_one(
                experiment="no_rag",
                rag_enabled=False,
                task={"task_id": "t4", "goal": "fix readme", "requires_kb": False},
                agent_loop_bin=agent_bin,
                repo="/tmp/fake",
                db_path=self.db_path,
                pr_mode="dry-run",
                max_iterations=1,
                kb_url="http://127.0.0.1:0",
                dry_run=False,
                task_timeout_sec=60,
                strict_mode=False,
                isolate_worktree=False,
                rate_limiter=limiter,
            )

        # If the semaphore was properly released, we can acquire it without blocking.
        acquired = limiter._sem.acquire(blocking=False)
        self.assertTrue(acquired, "rate limiter semaphore should be released after timeout")
        if acquired:
            limiter._sem.release()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python3 -m unittest eval.tests.test_run_one_integration.RunOneWorktreeIntegrationTests.test_rate_limiter_released_on_timeout -v`

Expected: PASS

- [ ] **Step 3: Run all integration tests together**

Run: `python3 -m unittest eval.tests.test_run_one_integration -v`

Expected: 5 tests, all PASS

- [ ] **Step 4: Run existing tests to verify no regression**

Run: `python3 -m unittest eval.tests.test_run_ab -v`

Expected: 31 tests, all PASS

Run: `cd eval/ab && python3 -m unittest test_run_ab_strict -v`

Expected: 9 tests, all PASS

- [ ] **Step 5: Commit**

```bash
git add eval/tests/test_run_one_integration.py
git commit -m "test: rate limiter released on timeout in run_one"
```

---

### Task 7: Final — add `if __name__` block and verify

**Files:**
- Modify: `eval/tests/test_run_one_integration.py`

- [ ] **Step 1: Append `__main__` block to end of file**

```python
if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run full suite one last time**

Run: `python3 -m unittest eval.tests.test_run_one_integration -v`

Expected: 5 tests, all PASS

- [ ] **Step 3: Commit**

```bash
git add eval/tests/test_run_one_integration.py
git commit -m "test: finalize run_one worktree integration tests"
```
