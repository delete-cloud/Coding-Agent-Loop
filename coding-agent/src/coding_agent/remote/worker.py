from __future__ import annotations

from pathlib import Path
from typing import Any

from coding_agent.runs import (
    REMOTE_LOOP_OWNERSHIP_RETIRED,
    RemoteLoopOwnershipRetired,
)


class AttachedExecutorError(RemoteLoopOwnershipRetired):
    """Raised when a retired attached-executor loop entrypoint is called."""


ExternalWorkerError = AttachedExecutorError


class AttachedExecutorConsumer:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise AttachedExecutorError(REMOTE_LOOP_OWNERSHIP_RETIRED)


RemoteWorkerConsumer = AttachedExecutorConsumer


async def run_local_attached_executor_once(
    *,
    base_url: str,
    headers: dict[str, str],
    repo_path: Path,
    goal: str,
    approval_policy: str,
    provider_name: str | None,
    model_name: str | None,
    base_url_override: str | None,
    max_steps: int,
    worker_id: str,
) -> int:
    del (
        base_url,
        headers,
        repo_path,
        goal,
        approval_policy,
        provider_name,
        model_name,
        base_url_override,
        max_steps,
        worker_id,
    )
    raise AttachedExecutorError(REMOTE_LOOP_OWNERSHIP_RETIRED)


async def run_local_worker_once(
    **kwargs: Any,
) -> int:
    """Compatibility wrapper for old local worker naming."""
    return await run_local_attached_executor_once(**kwargs)


async def run_attached_executor_loop(
    *,
    base_url: str,
    headers: dict[str, str],
    repo_path: Path,
    worker_id: str,
    once: bool,
    poll_interval_seconds: float,
) -> int:
    del base_url, headers, repo_path, worker_id, once, poll_interval_seconds
    raise AttachedExecutorError(REMOTE_LOOP_OWNERSHIP_RETIRED)


async def run_worker_loop(
    **kwargs: Any,
) -> int:
    """Compatibility wrapper for old worker naming."""
    return await run_attached_executor_loop(**kwargs)
