"""Session publish / GitHub PR routes and helpers."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, cast
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request

from coding_agent.environment import (
    WorkspaceBranchPublication,
)
from coding_agent.server.auth import (
    AuthContext,
    auth_context_from_headers,
)
from coding_agent.server.rate_limit import RateLimits, limiter
from coding_agent.server.schemas import (
    PublishSessionRequest,
    PublishSessionResponse,
)
from coding_agent.server.stores.session_owner_store import (
    SessionOwnershipConflictError,
)

from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME

from coding_agent.server.http.deps import (
    _get_visible_session,
    _key_error_detail,
    _owner_conflict_http_exception,
)
from coding_agent.server.http.workspace_retention import (
    _persist_workspace_publication_refs,
)
from fastapi import APIRouter

logger = logging.getLogger(LOGGER_NAME)
router = APIRouter()

_GITHUB_API_BASE_URL = "https://api.github.com"
_GITHUB_API_VERSION = "2022-11-28"
_GITHUB_SCP_REMOTE_RE = re.compile(r"^git@github\.com:(?P<path>[^:]+)$")


class GitHubPrUnsupportedError(ValueError):
    pass


class GitHubPrPublicationError(Exception):
    pass


@router.post("/sessions/{session_id}/publish", response_model=PublishSessionResponse)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def publish_session_result(
    request: Request,
    session_id: str,
    body: PublishSessionRequest,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> PublishSessionResponse:
    del request
    _ = await _get_visible_session(session_id, auth_context)
    branch_name = body.branch_name or f"coding-agent/session-{session_id}"
    commit_message = f"Apply coding-agent remote session {session_id} changes"
    publication_config = _bindings.module()._load_remote_publication_config()
    try:
        publication = await _bindings.module().session_manager.export_workspace_archive(
            session_id,
            lambda binding: _bindings.module().publish_workspace_branch_from_config(
                _bindings.module()._load_cloud_workspace_config(),
                publication_config,
                binding.workspace_id,
                branch_name,
                commit_message,
            ),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        if str(exc) == "turn already in progress":
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise

    if body.mode == "pr":
        return await _publish_session_pr_response(
            session_id,
            publication,
            publication_config,
        )

    await _persist_workspace_publication_refs(
        session_id,
        publication=publication,
        mode="branch",
        pr_url=None,
    )
    return PublishSessionResponse(
        session_id=session_id,
        mode="branch",
        status=publication.status,
        branch_name=publication.branch_name,
        pushed_ref=publication.pushed_ref,
        commit_sha=publication.commit_sha,
        remote_url=publication.remote_url,
        pr_url=None,
        error=publication.error,
    )


async def _publish_session_pr_response(
    session_id: str,
    publication: WorkspaceBranchPublication,
    publication_config: dict[str, Any],
) -> PublishSessionResponse:
    if publication.status == "partial":
        await _persist_workspace_publication_refs(
            session_id,
            publication=publication,
            mode="branch",
            pr_url=None,
        )
        return PublishSessionResponse(
            session_id=session_id,
            mode="pr",
            status="partial",
            branch_name=publication.branch_name,
            pushed_ref=publication.pushed_ref,
            commit_sha=publication.commit_sha,
            remote_url=publication.remote_url,
            pr_url=None,
            error=publication.error,
        )
    try:
        pr_url = await _create_github_pull_request(
            session_id,
            publication,
            publication_config,
        )
    except GitHubPrUnsupportedError as exc:
        await _persist_workspace_publication_refs(
            session_id,
            publication=publication,
            mode="branch",
            pr_url=None,
        )
        return PublishSessionResponse(
            session_id=session_id,
            mode="pr",
            status="unsupported",
            branch_name=publication.branch_name,
            pushed_ref=publication.pushed_ref,
            commit_sha=publication.commit_sha,
            remote_url=publication.remote_url,
            pr_url=None,
            error=str(exc),
        )
    except GitHubPrPublicationError as exc:
        await _persist_workspace_publication_refs(
            session_id,
            publication=publication,
            mode="branch",
            pr_url=None,
        )
        return PublishSessionResponse(
            session_id=session_id,
            mode="pr",
            status="failed",
            branch_name=publication.branch_name,
            pushed_ref=publication.pushed_ref,
            commit_sha=publication.commit_sha,
            remote_url=publication.remote_url,
            pr_url=None,
            error=str(exc),
        )
    await _persist_workspace_publication_refs(
        session_id,
        publication=publication,
        mode="pr",
        pr_url=pr_url,
    )
    return PublishSessionResponse(
        session_id=session_id,
        mode="pr",
        status="published",
        branch_name=publication.branch_name,
        pushed_ref=publication.pushed_ref,
        commit_sha=publication.commit_sha,
        remote_url=publication.remote_url,
        pr_url=pr_url,
        error=None,
    )


async def _create_github_pull_request(
    session_id: str,
    publication: WorkspaceBranchPublication,
    publication_config: dict[str, Any],
) -> str:
    github_config = publication_config.get("github")
    if not isinstance(github_config, dict) or github_config.get("enabled") is not True:
        raise GitHubPrUnsupportedError(
            "remote_publication.github.enabled=true is required for GitHub PR "
            "publication; branch was published and can be opened manually"
        )
    github_config = cast(dict[str, object], github_config)
    token_env = github_config.get("token_env")
    if not isinstance(token_env, str) or not token_env.strip():
        raise GitHubPrUnsupportedError(
            "remote_publication.github.token_env is required for GitHub PR "
            "publication; branch was published and can be opened manually"
        )
    token = os.environ.get(token_env.strip())
    if token is None or not token.strip():
        raise GitHubPrUnsupportedError(
            f"remote_publication.github.token_env is not set: {token_env.strip()}; "
            "branch was published and can be opened manually"
        )
    base_branch = github_config.get("base_branch")
    if not isinstance(base_branch, str) or not base_branch.strip():
        raise GitHubPrUnsupportedError(
            "remote_publication.github.base_branch is required for GitHub PR "
            "publication; branch was published and can be opened manually"
        )
    owner, repo = _github_repo_from_remote_url(publication.remote_url)
    try:
        async with _bindings.module().httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{_GITHUB_API_BASE_URL}/repos/{owner}/{repo}/pulls",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token.strip()}",
                    "X-GitHub-Api-Version": _GITHUB_API_VERSION,
                },
                json={
                    "title": f"Coding agent remote session {session_id}",
                    "head": publication.branch_name,
                    "base": base_branch.strip(),
                    "body": (
                        f"Remote coding-agent session `{session_id}` published "
                        f"commit `{publication.commit_sha}`."
                    ),
                },
            )
            response.raise_for_status()
    except Exception as exc:
        raise GitHubPrPublicationError(f"GitHub PR publication failed: {exc}") from exc
    payload = cast(object, response.json())
    if not isinstance(payload, dict):
        raise GitHubPrPublicationError(
            "GitHub PR publication failed: response must be a JSON object"
        )
    pr_url = payload.get("html_url")
    if not isinstance(pr_url, str) or not pr_url.strip():
        raise GitHubPrPublicationError(
            "GitHub PR publication failed: response missing html_url"
        )
    return pr_url


def _github_repo_from_remote_url(remote_url: str) -> tuple[str, str]:
    scp_match = _GITHUB_SCP_REMOTE_RE.fullmatch(remote_url.strip())
    if scp_match is not None:
        return _github_owner_repo_from_path(scp_match.group("path"))

    parsed = urlsplit(remote_url)
    if parsed.hostname != "github.com":
        raise GitHubPrUnsupportedError(
            "GitHub PR publication requires a github.com remote; branch was "
            "published and can be opened manually"
        )
    return _github_owner_repo_from_path(parsed.path)


def _github_owner_repo_from_path(path: str) -> tuple[str, str]:
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise GitHubPrUnsupportedError(
            "GitHub PR publication could not derive owner/repo from remote URL; "
            "branch was published and can be opened manually"
        )
    return parts[0], parts[1]


__all__ = [
    "GitHubPrPublicationError",
    "GitHubPrUnsupportedError",
    "_GITHUB_API_BASE_URL",
    "_GITHUB_API_VERSION",
    "_GITHUB_SCP_REMOTE_RE",
    "_create_github_pull_request",
    "_github_owner_repo_from_path",
    "_github_repo_from_remote_url",
    "_publish_session_pr_response",
    "publish_session_result",
]
