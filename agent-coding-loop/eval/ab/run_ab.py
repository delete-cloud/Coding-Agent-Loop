#!/usr/bin/env python3
"""Run minimal No-RAG vs RAG A/B tasks for agent-loop."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_GOAL_FILE_RE = re.compile(r"[A-Za-z0-9_./\\-]+\.[A-Za-z0-9_+-]+")
_ALLOWED_GOAL_EXTS = {
    ".md",
    ".go",
    ".py",
    ".rs",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".txt",
    ".sql",
    ".proto",
    ".java",
    ".kt",
    ".swift",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".sh",
}


def load_jsonl(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            text = line.strip()
            if not text:
                continue
            rows.append(json.loads(text))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def safe_slug(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(text or "").strip())
    value = value.strip("._-")
    if not value:
        return "task"
    return value[:64]


def normalize_rel_path(path: str) -> str:
    p = str(path or "").strip().replace("\\", "/")
    p = p.strip("`'\"")
    p = p.removeprefix("./")
    p = p.removeprefix("a/")
    p = p.removeprefix("b/")
    if not p:
        return ""
    if p.startswith("/"):
        return ""
    parts = [x for x in Path(p).parts if x not in {"", "."}]
    if not parts or any(x == ".." for x in parts):
        return ""
    return Path(*parts).as_posix()


def extract_goal_target_files(goal: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for token in _GOAL_FILE_RE.findall(str(goal or "")):
        p = normalize_rel_path(token)
        if not p:
            continue
        ext = Path(p).suffix.lower()
        if ext not in _ALLOWED_GOAL_EXTS:
            continue
        base = Path(p).name.lower()
        if "/" not in p and not (base == "readme.md" or base.startswith("readme.")):
            continue
        if "xxx." in p.lower():
            continue
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    out.sort()
    return out


def collect_overlay_paths(task: dict[str, Any], *, include_goal_targets: bool = True) -> list[str]:
    values: list[str] = []
    for raw in task.get("expected_citations", []) or []:
        p = normalize_rel_path(str(raw))
        if p:
            values.append(p)
    if include_goal_targets:
        for p in extract_goal_target_files(str(task.get("goal", ""))):
            values.append(p)
    if bool(task.get("requires_kb", False)):
        values.append("eval/ab/kb")
    dedup = sorted(set(values))
    return dedup


def should_copy_overlay_path(run_repo: str, rel: str, goal_targets: set[str]) -> bool:
    rel_norm = normalize_rel_path(rel)
    if not rel_norm:
        return False
    dst = Path(run_repo) / rel_norm
    # For goal-target files, preserve clean HEAD version when it already exists
    # in isolated worktree; only backfill missing target files from workspace.
    if rel_norm in goal_targets and dst.exists():
        return False
    return True


def materialize_overlay(base_repo: str, run_repo: str, task: dict[str, Any]) -> None:
    goal_targets = set(extract_goal_target_files(str(task.get("goal", ""))))
    for rel in collect_overlay_paths(task, include_goal_targets=True):
        if not should_copy_overlay_path(run_repo, rel, goal_targets):
            continue
        src = Path(base_repo) / rel
        if not src.exists():
            continue
        dst = Path(run_repo) / rel
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def resolve_worktree_base(base_repo: str) -> tuple[str, str]:
    top_proc = subprocess.run(
        ["git", "-C", base_repo, "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if top_proc.returncode != 0:
        detail = (top_proc.stderr or top_proc.stdout or "").strip()
        raise RuntimeError(f"resolve git toplevel failed: {detail}")
    top = (top_proc.stdout or "").strip()
    if not top:
        raise RuntimeError("resolve git toplevel failed: empty output")

    prefix_proc = subprocess.run(
        ["git", "-C", base_repo, "rev-parse", "--show-prefix"],
        capture_output=True,
        text=True,
        check=False,
    )
    if prefix_proc.returncode != 0:
        detail = (prefix_proc.stderr or prefix_proc.stdout or "").strip()
        raise RuntimeError(f"resolve git prefix failed: {detail}")
    prefix = (prefix_proc.stdout or "").strip()
    return top, prefix


def prepare_isolated_repo(base_repo: str, experiment: str, task_id: str) -> tuple[str, str, str, str]:
    temp_root = tempfile.mkdtemp(prefix=f"ab_{safe_slug(experiment)}_{safe_slug(task_id)}_")
    worktree_root = str(Path(temp_root) / "repo")
    git_top, git_prefix = resolve_worktree_base(base_repo)
    cmd = ["git", "-C", git_top, "worktree", "add", "--detach", worktree_root, "HEAD"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        shutil.rmtree(temp_root, ignore_errors=True)
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"prepare isolated worktree failed: {detail}")
    run_repo = str(Path(worktree_root) / git_prefix) if git_prefix else worktree_root
    if not Path(run_repo).exists():
        cleanup_isolated_repo(git_top, worktree_root, temp_root)
        raise RuntimeError(f"prepared isolated worktree missing repo path: {run_repo}")
    return run_repo, worktree_root, temp_root, git_top


def cleanup_isolated_repo(git_top: str, worktree_root: str, temp_root: str) -> None:
    if worktree_root:
        subprocess.run(
            ["git", "-C", git_top, "worktree", "remove", "--force", worktree_root],
            capture_output=True,
            text=True,
            check=False,
        )
    if temp_root:
        shutil.rmtree(temp_root, ignore_errors=True)


class _RateLimiter:
    """Token-bucket rate limiter for controlling concurrent task launches."""

    def __init__(self, max_concurrent: int, min_interval_sec: float = 0.0):
        self._sem = threading.Semaphore(max_concurrent)
        self._min_interval = max(0.0, min_interval_sec)
        self._lock = threading.Lock()
        self._last_launch = 0.0

    def acquire(self) -> None:
        self._sem.acquire()
        if self._min_interval > 0:
            with self._lock:
                now = time.monotonic()
                wait = self._last_launch + self._min_interval - now
                if wait > 0:
                    time.sleep(wait)
                self._last_launch = time.monotonic()

    def release(self) -> None:
        self._sem.release()


def build_goal(base_goal: str, rag_enabled: bool, requires_kb: bool) -> str:
    base_goal = base_goal.strip()
    if rag_enabled and requires_kb:
        rule = (
            "你必须先调用 kb_search 获取上下文，再修改代码。"
            "在最终说明里写出引用路径（例如 kb/xxx.md）。"
        )
    elif rag_enabled and not requires_kb:
        rule = "该任务是 repo-only 对照，不需要外部知识，禁止调用 kb_search。"
    else:
        rule = "本轮为 No-RAG 基线，禁止调用 kb_search，只能基于仓库内容完成任务。"
    return f"{base_goal}\n\n约束：{rule}"


def retrieval_mode_for_task(*, rag_enabled: bool, requires_kb: bool) -> str:
    if rag_enabled and requires_kb:
        return "prefetch"
    return "off"


def parse_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(data, dict):
        return data
    return {}


def normalize_citations(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip().replace("\\", "/")
        if not item:
            continue
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def citation_hit(token: str, citation_values: list[str], corpus_text: str) -> bool:
    low_token = token.lower()
    for item in citation_values:
        if low_token in item.lower():
            return True
    return low_token in (corpus_text or "").lower()


def evaluate_expectations(task: dict[str, Any], corpus_text: str, trace: dict[str, Any] | None = None) -> dict[str, Any]:
    text = (corpus_text or "").lower()
    requires_kb = bool(task.get("requires_kb", False))
    expected = [str(x).strip() for x in task.get("expected_citations", []) if str(x).strip()]
    expected_lower = [x.lower() for x in expected]

    trace = trace or {}
    citation_values = normalize_citations(list(trace.get("citations", [])))
    kb_search_calls = int(trace.get("kb_search_calls", 0) or 0)
    kb_signal = kb_search_calls > 0
    # Backward compatibility for old runs lacking tool_call status metadata.
    if requires_kb and not kb_signal and bool(citation_values):
        kb_signal = True
    found = 0
    for token in expected_lower:
        if citation_hit(token, citation_values, corpus_text):
            found += 1
            kb_signal = True

    citation_recall = 0.0
    if expected_lower:
        citation_recall = float(found) / float(len(expected_lower))

    out = {
        "requires_kb": requires_kb,
        "expected_citation_count": len(expected_lower),
        "found_citation_count": found,
        "citation_recall": citation_recall,
        "kb_signal": kb_signal,
    }
    return out


def evaluate_strict_reasons(
    *,
    strict_mode: bool,
    status: str,
    checks: dict[str, Any],
    summary_text: str,
    corpus_text: str,
    trace: dict[str, Any] | None = None,
) -> list[str]:
    if not strict_mode:
        return []
    reasons: list[str] = []
    trace = trace or {}
    has_structured_meta = bool(trace.get("meta_present", False))
    reviewer_used_fallback = bool(trace.get("reviewer_used_fallback", False))
    reviewer_decision = str(trace.get("reviewer_decision", "")).strip().lower()
    structured_citations = normalize_citations(list(trace.get("citations", [])))

    if status == "completed" and not has_structured_meta:
        reasons.append("missing_structured_meta")

    # Strict rule #1: only fallback "approve" decisions are forbidden.
    if status == "completed" and has_structured_meta and reviewer_decision == "approve" and reviewer_used_fallback:
        reasons.append("fallback_approve_forbidden")

    # Strict rule #2: KB-required task must cite at least one expected source token.
    if bool(checks.get("requires_kb", False)):
        if has_structured_meta:
            if len(structured_citations) <= 0:
                reasons.append("missing_citation")
        else:
            found = int(checks.get("found_citation_count", 0) or 0)
            if found <= 0:
                reasons.append("missing_citation")

    # Strict rule #3: KB-required task must have at least 1 real kb_search tool call
    # (not just backfill citations from ensureCitations).
    if bool(checks.get("requires_kb", False)):
        kb_calls = int(trace.get("kb_search_calls", 0) or 0)
        if kb_calls <= 0:
            reasons.append("no_real_kb_search")

    return reasons


def safe_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def aggregate_metrics(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_exp: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        exp = str(row.get("experiment", "")).strip() or "unknown"
        by_exp.setdefault(exp, []).append(row)

    out: dict[str, dict[str, Any]] = {}
    for exp, items in by_exp.items():
        total = len(items)
        completed = sum(1 for x in items if x.get("status") == "completed")
        durations = [float(x.get("duration_sec", 0.0)) for x in items]

        kb_items = [x for x in items if bool(x.get("requires_kb", False))]
        repo_items = [x for x in items if not bool(x.get("requires_kb", False))]

        kb_signal_rate = safe_mean([1.0 if bool(x.get("kb_signal", False)) else 0.0 for x in kb_items])
        citation_recall_avg = safe_mean([float(x.get("citation_recall", 0.0)) for x in kb_items])
        kb_search_calls_avg = safe_mean([float(x.get("kb_search_calls", 0)) for x in kb_items])
        repo_kb_overuse_rate = safe_mean([1.0 if bool(x.get("kb_signal", False)) else 0.0 for x in repo_items])

        out[exp] = {
            "total_tasks": total,
            "completed_tasks": completed,
            "pass_rate": float(completed) / float(total) if total else 0.0,
            "avg_duration_sec": safe_mean(durations),
            "kb_task_count": len(kb_items),
            "kb_signal_rate": kb_signal_rate,
            "citation_recall_avg": citation_recall_avg,
            "kb_search_calls_avg": kb_search_calls_avg,
            "repo_task_count": len(repo_items),
            "repo_kb_overuse_rate": repo_kb_overuse_rate,
        }
    return out


def read_run_context(db_path: str, run_id: str) -> tuple[float, str, dict[str, Any]]:
    if not run_id:
        return 0.0, "", {
            "meta_present": False,
            "fallback_used": False,
            "reviewer_used_fallback": False,
            "reviewer_decision": "",
            "citations": [],
            "kb_search_calls": 0,
        }
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        run = conn.execute(
            "select summary, created_at, updated_at from runs where id = ? limit 1",
            (run_id,),
        ).fetchone()
        if not run:
            return 0.0, "", {
                "meta_present": False,
                "fallback_used": False,
                "reviewer_used_fallback": False,
                "reviewer_decision": "",
                "citations": [],
                "kb_search_calls": 0,
            }

        created = float(run["created_at"] or 0.0)
        updated = float(run["updated_at"] or 0.0)
        duration = max(0.0, (updated - created) / 1000.0)
        pieces = [str(run["summary"] or "")]
        trace: dict[str, Any] = {
            "meta_present": False,
            "fallback_used": False,
            "reviewer_used_fallback": False,
            "reviewer_decision": "",
            "citations": [],
            "kb_search_calls": 0,
        }

        reviews = conn.execute(
            "select summary, findings_json from reviews where run_id = ?",
            (run_id,),
        ).fetchall()
        for row in reviews:
            pieces.append(str(row["summary"] or ""))
            pieces.append(str(row["findings_json"] or ""))

        tool_rows = conn.execute(
            "select tool, input_text, output_text, status from tool_calls where run_id = ?",
            (run_id,),
        ).fetchall()
        for row in tool_rows:
            tool_name = str(row["tool"] or "")
            pieces.append(tool_name)
            pieces.append(str(row["input_text"] or ""))
            pieces.append(str(row["output_text"] or ""))
            pieces.append(str(row["status"] or ""))
            tool_key = tool_name.strip().lower()
            if tool_key in {"coder_meta", "reviewer_meta"}:
                payload = parse_json_object(str(row["output_text"] or ""))
                if payload:
                    trace["meta_present"] = True
                    if bool(payload.get("used_fallback", False)):
                        trace["fallback_used"] = True
                    if tool_key == "coder_meta":
                        trace["citations"] = normalize_citations(
                            list(trace.get("citations", [])) + list(payload.get("citations", []))
                        )
                    if tool_key == "reviewer_meta":
                        trace["reviewer_used_fallback"] = bool(payload.get("used_fallback", False))
                        trace["reviewer_decision"] = str(payload.get("decision", "")).strip().lower()
            tool_status = str(row["status"] or "").strip().lower()
            if tool_key == "kb_search" and tool_status == "completed":
                trace["kb_search_calls"] = int(trace.get("kb_search_calls", 0) or 0) + 1

        return duration, "\n".join(pieces), trace
    finally:
        conn.close()


def render_markdown(report: dict[str, Any]) -> str:
    lines = []
    lines.append("# A/B Report (No-RAG vs RAG)")
    lines.append("")
    lines.append("| Experiment | Pass Rate | Avg Duration (s) | KB Signal Rate | Citation Recall | KB Search Calls | Repo KB Overuse |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for exp in ("no_rag", "rag"):
        item = report.get("metrics", {}).get(exp)
        if not item:
            continue
        lines.append(
            f"| {exp} | {item['pass_rate']:.3f} | {item['avg_duration_sec']:.2f} | "
            f"{item['kb_signal_rate']:.3f} | {item['citation_recall_avg']:.3f} | "
            f"{item.get('kb_search_calls_avg', 0.0):.2f} | "
            f"{item['repo_kb_overuse_rate']:.3f} |"
        )
    lines.append("")
    lines.append("## Notes")
    lines.append("- Strict mode prefers structured `coder_meta` / `reviewer_meta` records from state.db.")
    lines.append("- Citation matching prioritizes structured `citations[]`, with text as backward-compatible fallback.")
    return "\n".join(lines) + "\n"


def parse_result(stdout: str) -> dict[str, Any]:
    text = (stdout or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # try to parse trailing JSON object
        start = text.rfind("{")
        if start >= 0:
            try:
                return json.loads(text[start:])
            except json.JSONDecodeError:
                return {}
    return {}


def run_one(
    *,
    experiment: str,
    rag_enabled: bool,
    task: dict[str, Any],
    agent_loop_bin: str,
    repo: str,
    db_path: str,
    pr_mode: str,
    max_iterations: int,
    kb_url: str,
    dry_run: bool,
    task_timeout_sec: int,
    strict_mode: bool,
    isolate_worktree: bool,
    rate_limiter: _RateLimiter | None = None,
) -> dict[str, Any]:
    requires_kb = bool(task.get("requires_kb", False))
    goal = build_goal(str(task.get("goal", "")), rag_enabled=rag_enabled, requires_kb=requires_kb)
    task_id = str(task.get("task_id", ""))

    run_repo = repo
    isolated_root = ""
    isolated_worktree_root = ""
    isolated_git_top = ""
    isolate_err = ""
    if (not dry_run) and isolate_worktree:
        try:
            run_repo, isolated_worktree_root, isolated_root, isolated_git_top = prepare_isolated_repo(repo, experiment, task_id)
            materialize_overlay(repo, run_repo, task)
        except Exception as exc:  # noqa: BLE001
            isolate_err = str(exc)

    if isolate_err:
        checks = evaluate_expectations(task, corpus_text=isolate_err, trace={})
        strict_reasons = evaluate_strict_reasons(
            strict_mode=bool(strict_mode),
            status="failed",
            checks=checks,
            summary_text=isolate_err,
            corpus_text=isolate_err,
            trace={},
        )
        return {
            "experiment": experiment,
            "task_id": task_id,
            "status": "failed",
            "duration_sec": 0.0,
            "run_id": "",
            "summary": "isolate worktree failed",
            "exit_code": 1,
            "stderr_preview": isolate_err[:600],
            "requires_kb": checks["requires_kb"],
            "kb_signal": checks["kb_signal"],
            "citation_recall": checks["citation_recall"],
            "expected_citation_count": checks["expected_citation_count"],
            "found_citation_count": checks["found_citation_count"],
            "strict_mode": bool(strict_mode),
            "strict_reasons": strict_reasons,
            "fallback_used": False,
            "structured_citations": [],
            "kb_search_calls": 0,
        }

    cmd = [
        agent_loop_bin,
        "run",
        "--goal",
        goal,
        "--repo",
        run_repo,
        "--pr-mode",
        pr_mode,
        "--retrieval-mode",
        retrieval_mode_for_task(rag_enabled=rag_enabled, requires_kb=requires_kb),
        "--max-iterations",
        str(max_iterations),
    ]
    if str(task.get("test_cmd", "")).strip():
        cmd += ["--test-cmd", str(task["test_cmd"]).strip()]

    if dry_run:
        row = {
            "experiment": experiment,
            "task_id": task_id,
            "status": "dry_run",
            "duration_sec": 0.0,
            "run_id": "",
            "summary": "dry run only",
            "command": cmd,
            "requires_kb": bool(task.get("requires_kb", False)),
            "kb_signal": False,
            "citation_recall": 0.0,
            "strict_mode": bool(strict_mode),
            "strict_reasons": [],
            "fallback_used": False,
            "structured_citations": [],
            "kb_search_calls": 0,
        }
        if isolated_root:
            cleanup_isolated_repo(isolated_git_top, isolated_worktree_root, isolated_root)
        return row

    env = os.environ.copy()
    if rag_enabled:
        env["AGENT_LOOP_KB_URL"] = kb_url
    else:
        env["AGENT_LOOP_KB_URL"] = "http://127.0.0.1:0"

    if rate_limiter is not None:
        rate_limiter.acquire()
    log.info("start %s/%s", experiment, task_id)
    t0 = time.time()
    timeout = task_timeout_sec if task_timeout_sec > 0 else None
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        if rate_limiter is not None:
            rate_limiter.release()
        wall_duration = max(0.0, time.time() - t0)
        parsed = parse_result(e.stdout or "")
        run_id = str(parsed.get("run_id", "")).strip()
        summary = str(parsed.get("summary", "")).strip()
        if not summary:
            summary = f"task timed out after {task_timeout_sec}s"
        db_duration, corpus, trace = read_run_context(db_path=db_path, run_id=run_id)
        duration = db_duration if db_duration > 0 else wall_duration
        checks = evaluate_expectations(task, corpus_text=corpus + "\n" + summary, trace=trace)
        strict_reasons = evaluate_strict_reasons(
            strict_mode=bool(strict_mode),
            status="failed",
            checks=checks,
            summary_text=summary,
            corpus_text=corpus,
            trace=trace,
        )
        row = {
            "experiment": experiment,
            "task_id": task_id,
            "status": "failed",
            "duration_sec": duration,
            "run_id": run_id,
            "summary": summary,
            "exit_code": 124,
            "timed_out": True,
            "stderr_preview": (e.stderr or "").strip()[:600],
            "requires_kb": checks["requires_kb"],
            "kb_signal": checks["kb_signal"],
            "citation_recall": checks["citation_recall"],
            "expected_citation_count": checks["expected_citation_count"],
            "found_citation_count": checks["found_citation_count"],
            "strict_mode": bool(strict_mode),
            "strict_reasons": strict_reasons,
            "fallback_used": bool(trace.get("fallback_used", False)),
            "structured_citations": normalize_citations(list(trace.get("citations", []))),
            "kb_search_calls": int(trace.get("kb_search_calls", 0) or 0),
        }
        if isolated_root:
            cleanup_isolated_repo(isolated_git_top, isolated_worktree_root, isolated_root)
        log.info("done  %s/%s (timeout)", experiment, task_id)
        return row
    if rate_limiter is not None:
        rate_limiter.release()
    wall_duration = max(0.0, time.time() - t0)
    parsed = parse_result(proc.stdout)
    run_id = str(parsed.get("run_id", "")).strip()
    status = str(parsed.get("status", "")).strip() or "failed"
    summary = str(parsed.get("summary", "")).strip()

    db_duration, corpus, trace = read_run_context(db_path=db_path, run_id=run_id)
    duration = db_duration if db_duration > 0 else wall_duration
    checks = evaluate_expectations(task, corpus_text=corpus + "\n" + summary, trace=trace)
    strict_reasons = evaluate_strict_reasons(
        strict_mode=bool(strict_mode),
        status=status,
        checks=checks,
        summary_text=summary,
        corpus_text=corpus,
        trace=trace,
    )
    if strict_reasons and status == "completed":
        status = "failed"

    row = {
        "experiment": experiment,
        "task_id": task_id,
        "status": status,
        "duration_sec": duration,
        "run_id": run_id,
        "summary": summary,
        "exit_code": proc.returncode,
        "stderr_preview": (proc.stderr or "").strip()[:600],
        "requires_kb": checks["requires_kb"],
        "kb_signal": checks["kb_signal"],
        "citation_recall": checks["citation_recall"],
        "expected_citation_count": checks["expected_citation_count"],
        "found_citation_count": checks["found_citation_count"],
        "strict_mode": bool(strict_mode),
        "strict_reasons": strict_reasons,
        "fallback_used": bool(trace.get("fallback_used", False)),
        "structured_citations": normalize_citations(list(trace.get("citations", []))),
        "kb_search_calls": int(trace.get("kb_search_calls", 0) or 0),
    }
    if isolated_root:
        cleanup_isolated_repo(isolated_git_top, isolated_worktree_root, isolated_root)
    log.info("done  %s/%s status=%s %.1fs", experiment, task_id, status, duration)
    return row


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run minimal No-RAG vs RAG benchmark tasks.")
    p.add_argument("--tasks", default="eval/ab/minimal_tasks.jsonl", help="Task file (JSONL)")
    p.add_argument("--agent-loop-bin", default="./agent-loop", help="Path to agent-loop binary")
    p.add_argument("--repo", required=True, help="Repository path to run tasks against")
    p.add_argument("--db-path", default=".agent-loop-artifacts/state.db", help="state.db path")
    p.add_argument("--output-dir", default="eval/reports/ab", help="Output directory")
    p.add_argument("--kb-url", default="http://127.0.0.1:8788", help="KB URL for RAG run")
    p.add_argument("--pr-mode", default="dry-run", help="PR mode for agent-loop run")
    p.add_argument("--max-iterations", type=int, default=2, help="Max iterations per task")
    p.add_argument("--task-timeout-sec", type=int, default=180, help="Per task timeout in seconds; <=0 disables timeout")
    p.add_argument("--strict-mode", action="store_true", help="Enable strict evaluation: forbid fallback approve and require citations for KB tasks")
    p.add_argument(
        "--isolate-worktree",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run each task in an isolated temporary git worktree (default: enabled)",
    )
    p.add_argument("--concurrency", type=int, default=1, help="Max parallel tasks (default: 1 = serial)")
    p.add_argument("--launch-interval", type=float, default=2.0, help="Min seconds between launching tasks (rate-limit friendly)")
    p.add_argument("--only", choices=["no_rag", "rag"], help="Run only one experiment")
    p.add_argument("--dry-run", action="store_true", help="Only print commands, do not execute")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    args = parse_args()
    tasks = load_jsonl(args.tasks)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    experiments = [("no_rag", False), ("rag", True)]
    if args.only:
        experiments = [x for x in experiments if x[0] == args.only]

    concurrency = max(1, args.concurrency)
    launch_interval = max(0.0, args.launch_interval)
    limiter = _RateLimiter(max_concurrent=concurrency, min_interval_sec=launch_interval)

    # Build the full job list: [(experiment, rag_enabled, task), ...]
    jobs: list[tuple[str, bool, dict[str, Any]]] = []
    for exp_name, rag_enabled in experiments:
        for task in tasks:
            jobs.append((exp_name, rag_enabled, task))

    total = len(jobs)
    log.info("scheduling %d jobs (concurrency=%d, interval=%.1fs)", total, concurrency, launch_interval)

    if concurrency <= 1:
        # Serial path: simple loop, no thread overhead
        rows: list[dict[str, Any]] = []
        for exp_name, rag_enabled, task in jobs:
            row = run_one(
                experiment=exp_name,
                rag_enabled=rag_enabled,
                task=task,
                agent_loop_bin=args.agent_loop_bin,
                repo=args.repo,
                db_path=args.db_path,
                pr_mode=args.pr_mode,
                max_iterations=args.max_iterations,
                kb_url=args.kb_url,
                dry_run=bool(args.dry_run),
                task_timeout_sec=args.task_timeout_sec,
                strict_mode=bool(args.strict_mode),
                isolate_worktree=bool(args.isolate_worktree),
            )
            rows.append(row)
    else:
        # Parallel path: ThreadPoolExecutor + rate limiter
        # Each task runs in its own git worktree so no file conflicts.
        # The rate limiter controls how many agent-loop processes hit the
        # upstream LLM gateway concurrently and ensures a minimum interval
        # between launches to avoid burst-triggered throttling.
        rows_by_idx: dict[int, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            future_to_idx = {}
            for idx, (exp_name, rag_enabled, task) in enumerate(jobs):
                fut = pool.submit(
                    run_one,
                    experiment=exp_name,
                    rag_enabled=rag_enabled,
                    task=task,
                    agent_loop_bin=args.agent_loop_bin,
                    repo=args.repo,
                    db_path=args.db_path,
                    pr_mode=args.pr_mode,
                    max_iterations=args.max_iterations,
                    kb_url=args.kb_url,
                    dry_run=bool(args.dry_run),
                    task_timeout_sec=args.task_timeout_sec,
                    strict_mode=bool(args.strict_mode),
                    isolate_worktree=True,  # force isolation in parallel mode
                    rate_limiter=limiter,
                )
                future_to_idx[fut] = idx
            for fut in as_completed(future_to_idx):
                idx = future_to_idx[fut]
                try:
                    rows_by_idx[idx] = fut.result()
                except Exception as exc:
                    exp_name, _, task = jobs[idx]
                    task_id = str(task.get("task_id", ""))
                    log.error("job %s/%s crashed: %s", exp_name, task_id, exc)
                    rows_by_idx[idx] = {
                        "experiment": exp_name,
                        "task_id": task_id,
                        "status": "failed",
                        "duration_sec": 0.0,
                        "run_id": "",
                        "summary": f"worker exception: {exc}",
                    }
        rows = [rows_by_idx[i] for i in range(total)]

    log.info("all %d jobs finished", total)

    metrics = aggregate_metrics(rows)
    report = {
        "meta": {
            "tasks": args.tasks,
            "agent_loop_bin": args.agent_loop_bin,
            "repo": args.repo,
            "db_path": args.db_path,
            "kb_url": args.kb_url,
            "pr_mode": args.pr_mode,
            "max_iterations": args.max_iterations,
            "task_timeout_sec": args.task_timeout_sec,
            "strict_mode": bool(args.strict_mode),
            "isolate_worktree": bool(args.isolate_worktree),
            "concurrency": concurrency,
            "launch_interval": launch_interval,
            "dry_run": bool(args.dry_run),
        },
        "metrics": metrics,
        "rows": rows,
    }

    raw_path = output_dir / "ab_raw_runs.jsonl"
    json_path = output_dir / "ab_report.json"
    md_path = output_dir / "ab_report.md"

    write_jsonl(raw_path, rows)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")

    print(json.dumps({"metrics": metrics, "report_json": str(json_path), "report_md": str(md_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
