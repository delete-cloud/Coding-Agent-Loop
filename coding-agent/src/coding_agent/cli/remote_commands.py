from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import NoReturn, cast

import click

from coding_agent.remote.approval import APPROVAL_POLICIES

REMOTE_APPROVAL_CHOICES = click.Choice(APPROVAL_POLICIES)


@click.group()
def remote() -> None:
    """Manage and use remote Coding Agent servers."""


@remote.command("add")
@click.argument("name")
@click.argument("url")
@click.option("--token", default=None, help="Bearer token for the remote server")
def remote_add(name: str, url: str, token: str | None) -> None:
    """Add or update a named remote endpoint."""
    from coding_agent.remote.client import add_remote

    endpoint = add_remote(name, url, token)
    click.echo(f"Added remote {endpoint.name}: {endpoint.url}")


@remote.command("list")
def remote_list() -> None:
    """List named remote endpoints."""
    from coding_agent.remote.client import load_remotes

    remotes = load_remotes()
    if not remotes:
        click.echo("No remotes configured.")
        return
    for endpoint in remotes.values():
        auth = "token" if endpoint.token is not None else "no-token"
        click.echo(f"{endpoint.name}\t{endpoint.url}\t{auth}")


@remote.command("remove")
@click.argument("name")
def remote_remove(name: str) -> None:
    """Remove a named remote endpoint."""
    from coding_agent.remote.client import remove_remote

    remove_remote(name)
    click.echo(f"Removed remote {name}")


@remote.command("repl")
@click.argument("name")
@click.option(
    "--repo",
    default=None,
    help="Upload a local workspace snapshot and download the final remote workspace into it.",
)
@click.option(
    "--empty-workspace",
    is_flag=True,
    help="Create an empty server-side Docker workspace.",
)
@click.option(
    "--runtime",
    "runtime_profile",
    default=None,
    help="Use a server allowlisted runtime profile for the remote workspace.",
)
@click.option(
    "--goal", required=True, help="Initial prompt to send to the remote session"
)
@click.option(
    "--approval",
    "approval_policy",
    default="auto",
    show_default=True,
    type=REMOTE_APPROVAL_CHOICES,
    help="Remote tool approval policy for the created session.",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Download and overwrite the local workspace without confirmation.",
)
def remote_repl(
    name: str,
    repo: str | None,
    empty_workspace: bool,
    runtime_profile: str | None,
    goal: str,
    approval_policy: str,
    yes: bool,
) -> None:
    """Compatibility alias for remote run; creates a one-shot remote run."""
    _remote_run_once(
        name=name,
        repo=repo,
        empty_workspace=empty_workspace,
        runtime_profile=runtime_profile,
        goal=goal,
        approval_policy=approval_policy,
        yes=yes,
        download_results=repo is not None,
        cleanup_on_success=True,
    )


@remote.command("run")
@click.argument("name")
@click.option(
    "--repo",
    default=None,
    help="Use a clean Git repo as the remote workspace source. Results stay remote unless --download is passed.",
)
@click.option(
    "--empty-workspace",
    is_flag=True,
    help="Create an empty server-side Docker workspace.",
)
@click.option(
    "--runtime",
    "runtime_profile",
    default=None,
    help="Use a server allowlisted runtime profile for the remote workspace.",
)
@click.option(
    "--goal", required=True, help="Initial prompt to send to the remote session"
)
@click.option(
    "--approval",
    "approval_policy",
    default="auto",
    show_default=True,
    type=REMOTE_APPROVAL_CHOICES,
    help="Remote tool approval policy for the created session.",
)
@click.option(
    "--yes",
    is_flag=True,
    help="When used with --download, overwrite the local workspace without confirmation.",
)
@click.option(
    "--download",
    "download_results",
    is_flag=True,
    help="Download and overwrite --repo after the remote run completes.",
)
@click.option(
    "--snapshot-fallback",
    is_flag=True,
    help="Use archive upload for local-only or dirty repos instead of Git-backed publication input.",
)
def remote_run(
    name: str,
    repo: str | None,
    empty_workspace: bool,
    runtime_profile: str | None,
    goal: str,
    approval_policy: str,
    yes: bool,
    download_results: bool,
    snapshot_fallback: bool,
) -> None:
    """Create a one-shot remote run and stream one prompt."""
    _remote_run_once(
        name=name,
        repo=repo,
        empty_workspace=empty_workspace,
        runtime_profile=runtime_profile,
        goal=goal,
        approval_policy=approval_policy,
        yes=yes,
        download_results=download_results,
        cleanup_on_success=download_results,
        snapshot_fallback=snapshot_fallback,
    )


def _remote_run_once(
    *,
    name: str,
    repo: str | None,
    empty_workspace: bool,
    runtime_profile: str | None,
    goal: str,
    approval_policy: str,
    yes: bool,
    download_results: bool,
    cleanup_on_success: bool,
    snapshot_fallback: bool = True,
) -> None:
    from coding_agent.remote.client import (
        auth_headers,
        create_remote_session,
        delete_remote_session,
        get_remote,
        stream_prompt,
    )
    from coding_agent.workspace_archive import (
        create_workspace_archive_base64,
    )

    if repo is not None and empty_workspace:
        raise click.ClickException(
            "Pass either --repo to use a local repo or --empty-workspace, not both."
        )
    if repo is None and not empty_workspace:
        raise click.ClickException(
            "Pass --repo to use a local repo or --empty-workspace to create a blank remote workspace."
        )
    if download_results and repo is None:
        raise click.ClickException("Pass --repo with --download.")

    endpoint = get_remote(name)
    headers = auth_headers(endpoint)
    repo_path: Path | None = None
    snapshot_archive_base64: str | None = None
    workspace_source: dict[str, object] | None = None
    if repo is not None:
        repo_path = Path(repo).expanduser().resolve()
        if not repo_path.is_dir():
            raise click.ClickException(f"--repo must be an existing directory: {repo}")
        if snapshot_fallback:
            try:
                snapshot_archive_base64 = create_workspace_archive_base64(repo_path)
            except ValueError as exc:
                raise click.ClickException(str(exc)) from exc
        else:
            workspace_source = _git_workspace_source_for_remote_run(
                repo_path,
                runtime_profile=runtime_profile,
            )

    session_id = create_remote_session(
        endpoint,
        snapshot_archive_base64=snapshot_archive_base64,
        workspace_source=workspace_source,
        approval_policy=approval_policy,
        runtime_profile=None if workspace_source is not None else runtime_profile,
    )
    click.echo(f"Created one-shot remote session {session_id} on remote {name}")
    status: int | None = None
    stream_error: Exception | None = None
    deferred_error: click.ClickException | None = None
    workspace_restore_failed = False
    try:
        status = stream_prompt(
            base_url=endpoint.url,
            session_id=session_id,
            prompt=goal,
            headers=headers,
        )
    except Exception as exc:
        stream_error = exc
    finally:
        if repo_path is not None and download_results:
            try:
                _download_and_restore_workspace(
                    base_url=endpoint.url,
                    session_id=session_id,
                    headers=headers,
                    repo_path=repo_path,
                    yes=yes,
                )
            except Exception as exc:
                workspace_restore_failed = True
                if stream_error is not None:
                    stream_error.add_note(f"Workspace download also failed: {exc}")
                else:
                    deferred_error = click.ClickException(str(exc))
        if workspace_restore_failed:
            click.echo(
                f"Remote session {session_id} left open; local workspace restore failed."
            )
            if repo_path is not None:
                click.echo(
                    "Retry download with: "
                    + _format_cli_command(
                        [
                            "python",
                            "-m",
                            "coding_agent",
                            "remote",
                            "download",
                            name,
                            "--session",
                            session_id,
                            "--repo",
                            str(repo_path),
                        ]
                    )
                )
            click.echo(
                "Continue prompting with: "
                + _format_cli_command(
                    [
                        "python",
                        "-m",
                        "coding_agent",
                        "remote",
                        "attach",
                        name,
                        session_id,
                    ]
                )
            )
        elif cleanup_on_success:
            try:
                delete_remote_session(
                    base_url=endpoint.url,
                    session_id=session_id,
                    headers=headers,
                )
                click.echo(f"Cleaned up remote session {session_id}")
            except Exception as exc:
                if stream_error is not None:
                    stream_error.add_note(f"Remote session cleanup also failed: {exc}")
                elif deferred_error is not None:
                    deferred_error.add_note(
                        f"Remote session cleanup also failed: {exc}"
                    )
                else:
                    raise
        else:
            has_git_workspace_source = (
                workspace_source is not None and workspace_source.get("kind") == "git"
            )
            _print_remote_result_next_steps(
                remote_name=name,
                session_id=session_id,
                repo_path=repo_path,
                can_diff_patch=has_git_workspace_source,
                can_publish_branch=has_git_workspace_source,
            )
    if stream_error is not None:
        raise stream_error
    if deferred_error is not None:
        raise deferred_error
    assert status is not None
    raise SystemExit(status)


@remote.command("local-run")
@click.argument("name")
@click.option("--repo", default=".", help="Run tools against this local workspace.")
@click.option("--goal", required=True, help="Prompt to execute locally.")
@click.option(
    "--approval",
    "approval_policy",
    default="yolo",
    show_default=True,
    type=REMOTE_APPROVAL_CHOICES,
    help="Local attached executor approval policy.",
)
@click.option("--max-steps", default=30, show_default=True, help="Max steps per turn.")
@click.option(
    "--executor-id",
    "--worker-id",
    "worker_id",
    default=None,
    help="Stable local attached executor id.",
)
def remote_local_run(
    name: str,
    repo: str,
    goal: str,
    approval_policy: str,
    max_steps: int,
    worker_id: str | None,
) -> None:
    """Create an o6n-managed session and execute it in the local workspace."""
    import asyncio
    import uuid

    from coding_agent.remote.client import auth_headers, get_remote
    from coding_agent.remote.worker import run_local_attached_executor_once

    endpoint = get_remote(name)
    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.is_dir():
        raise click.ClickException(f"--repo must be an existing directory: {repo}")
    resolved_executor_id = worker_id or f"local-cli-{uuid.uuid4().hex}"
    status = asyncio.run(
        run_local_attached_executor_once(
            base_url=endpoint.url,
            headers=auth_headers(endpoint),
            repo_path=repo_path,
            goal=goal,
            approval_policy=approval_policy,
            provider_name=None,
            model_name=None,
            base_url_override=None,
            max_steps=max_steps,
            worker_id=resolved_executor_id,
        )
    )
    raise SystemExit(status)


def _run_remote_attached_executor(
    *,
    name: str,
    repo: str,
    executor_id: str,
    once: bool,
    poll_interval: float,
) -> None:
    import asyncio

    from coding_agent.remote.client import auth_headers, get_remote
    from coding_agent.remote.worker import run_attached_executor_loop

    endpoint = get_remote(name)
    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.is_dir():
        raise click.ClickException(f"--repo must be an existing directory: {repo}")
    status = asyncio.run(
        run_attached_executor_loop(
            base_url=endpoint.url,
            headers=auth_headers(endpoint),
            repo_path=repo_path,
            worker_id=executor_id,
            once=once,
            poll_interval_seconds=poll_interval,
        )
    )
    raise SystemExit(status)


@remote.command("executor")
@click.argument("name")
@click.option("--repo", default=".", help="Run claimed jobs against this workspace.")
@click.option("--executor-id", required=True, help="Stable local attached executor id.")
@click.option(
    "--once",
    is_flag=True,
    help="Exit after one claimed run or after one empty poll.",
)
@click.option(
    "--poll-interval",
    default=2.0,
    show_default=True,
    type=click.FloatRange(min=0.0, min_open=True),
    help="Seconds between empty claim polls.",
)
def remote_executor(
    name: str,
    repo: str,
    executor_id: str,
    once: bool,
    poll_interval: float,
) -> None:
    """Run a local attached executor for o6n-managed sessions."""
    _run_remote_attached_executor(
        name=name,
        repo=repo,
        executor_id=executor_id,
        once=once,
        poll_interval=poll_interval,
    )


@remote.command("worker")
@click.argument("name")
@click.option("--repo", default=".", help="Run claimed jobs against this workspace.")
@click.option("--worker-id", required=True, help="Stable external worker id.")
@click.option(
    "--once",
    is_flag=True,
    help="Exit after one claimed run or after one empty poll.",
)
@click.option(
    "--poll-interval",
    default=2.0,
    show_default=True,
    type=click.FloatRange(min=0.0, min_open=True),
    help="Seconds between empty claim polls.",
)
def remote_worker(
    name: str,
    repo: str,
    worker_id: str,
    once: bool,
    poll_interval: float,
) -> None:
    """Compatibility alias for `remote executor`."""
    _run_remote_attached_executor(
        name=name,
        repo=repo,
        executor_id=worker_id,
        once=once,
        poll_interval=poll_interval,
    )


def _format_cli_command(args: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in args)


def _print_mapping(payload: dict[str, object]) -> None:
    for key in sorted(payload):
        value = payload[key]
        if isinstance(value, dict | list):
            click.echo(f"{key}:")
            click.echo(_json_dumps_pretty(value))
        else:
            click.echo(f"{key}: {value}")


def _json_dumps_pretty(value: object) -> str:
    import json

    return json.dumps(value, indent=2, sort_keys=True)


def _git_workspace_source_for_remote_run(
    repo_path: Path,
    *,
    runtime_profile: str | None,
) -> dict[str, object]:
    try:
        top_level = _run_git_for_remote_source(
            repo_path, ["rev-parse", "--show-toplevel"]
        )
        if Path(top_level).resolve() != repo_path:
            raise click.ClickException(
                "--repo must point at the Git worktree root for Git-backed remote runs."
            )
        status = _run_git_for_remote_source(repo_path, ["status", "--porcelain=v1"])
        if status:
            raise click.ClickException(
                "remote run --repo requires a clean Git working tree. "
                "Commit, stash, or pass --snapshot-fallback to upload an archive instead."
            )
        remote_url = _run_git_for_remote_source(
            repo_path, ["config", "--get", "remote.origin.url"]
        )
        if not remote_url:
            raise click.ClickException(
                "remote run --repo requires remote.origin.url. "
                "Pass --snapshot-fallback for local-only repositories."
            )
        base_ref = _run_git_for_remote_source(
            repo_path, ["rev-parse", "--abbrev-ref", "HEAD"]
        )
        if base_ref == "HEAD":
            raise click.ClickException(
                "remote run --repo requires a named Git branch. "
                "Pass --snapshot-fallback for detached HEAD worktrees."
            )
        base_sha = _run_git_for_remote_source(repo_path, ["rev-parse", "HEAD"])
        _run_git_for_remote_source(
            repo_path,
            ["merge-base", "--is-ancestor", "HEAD", f"refs/remotes/origin/{base_ref}"],
        )
    except click.ClickException:
        raise
    except subprocess.CalledProcessError as exc:
        message = _git_remote_source_error_message(exc)
        raise click.ClickException(message) from exc
    workspace_source: dict[str, object] = {
        "kind": "git",
        "remote_url": remote_url,
        "base_ref": base_ref,
        "base_sha": base_sha,
    }
    if runtime_profile is not None:
        workspace_source["runtime_profile"] = runtime_profile
    return workspace_source


def _run_git_for_remote_source(repo_path: Path, args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            shell=False,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise click.ClickException(
            "git not found; please install git or adjust PATH."
        ) from exc
    return result.stdout.strip()


def _git_remote_source_error_message(exc: subprocess.CalledProcessError) -> str:
    stderr = exc.stderr.strip() if isinstance(exc.stderr, str) else ""
    command = " ".join(str(part) for part in exc.cmd)
    if "config --get remote.origin.url" in command:
        return (
            "remote run --repo requires remote.origin.url. "
            "Pass --snapshot-fallback for local-only repositories."
        )
    if "merge-base" in command:
        return (
            "remote run --repo requires HEAD to be available from origin. "
            "Push the branch or pass --snapshot-fallback to upload an archive instead."
        )
    if stderr:
        return f"Git-backed remote run setup failed: {stderr}"
    return "Git-backed remote run setup failed. Pass --snapshot-fallback to upload an archive instead."


def _print_remote_result_next_steps(
    *,
    remote_name: str,
    session_id: str,
    repo_path: Path | None,
    can_diff_patch: bool,
    can_publish_branch: bool,
) -> None:
    click.echo(f"Remote session {session_id} left open for result inspection.")
    click.echo(
        "Show session result: "
        + _format_cli_command(
            [
                "python",
                "-m",
                "coding_agent",
                "remote",
                "result",
                remote_name,
                "--session",
                session_id,
            ]
        )
    )
    if can_diff_patch:
        click.echo(
            "Show changed files: "
            + _format_cli_command(
                [
                    "python",
                    "-m",
                    "coding_agent",
                    "remote",
                    "diff",
                    remote_name,
                    "--session",
                    session_id,
                ]
            )
        )
        click.echo(
            "Export patch: "
            + _format_cli_command(
                [
                    "python",
                    "-m",
                    "coding_agent",
                    "remote",
                    "patch",
                    remote_name,
                    "--session",
                    session_id,
                ]
            )
        )
    if can_publish_branch:
        click.echo(
            "Publish review branch: "
            + _format_cli_command(
                [
                    "python",
                    "-m",
                    "coding_agent",
                    "remote",
                    "publish",
                    remote_name,
                    "--session",
                    session_id,
                    "--branch",
                    f"coding-agent/session-{session_id}",
                ]
            )
        )
    if repo_path is not None:
        click.echo(
            "Fallback archive download: "
            + _format_cli_command(
                [
                    "python",
                    "-m",
                    "coding_agent",
                    "remote",
                    "download",
                    remote_name,
                    "--session",
                    session_id,
                    "--repo",
                    str(repo_path),
                ]
            )
        )
    click.echo(
        "Close session: "
        + _format_cli_command(
            [
                "python",
                "-m",
                "coding_agent",
                "remote",
                "sessions",
                "close",
                remote_name,
                session_id,
            ]
        )
    )


@remote.group("sessions")
def remote_sessions() -> None:
    """Inspect and control remote sessions."""


@remote_sessions.command("list")
@click.argument("name")
def remote_sessions_list(name: str) -> None:
    """List sessions visible to the remote token."""
    _print_remote_sessions(name)


def _print_remote_sessions(name: str) -> None:
    from coding_agent.remote.client import get_remote, list_remote_sessions

    endpoint = get_remote(name)
    sessions = list_remote_sessions(endpoint)
    if not sessions:
        click.echo("No remote sessions found.")
        return
    for session in sessions:
        click.echo(
            "\t".join(
                [
                    str(session.get("session_id", "")),
                    str(session.get("status", "")),
                    str(session.get("turn_status", "")),
                    str(session.get("workspace_id", "")),
                    str(session.get("last_run_status", "")),
                    "resumable" if session.get("resumable") is True else "",
                    str(session.get("latest_checkpoint_id", "")),
                ]
            )
        )


@remote_sessions.command("status")
@click.argument("name")
@click.argument("session_id")
def remote_sessions_status(name: str, session_id: str) -> None:
    """Show one remote session."""
    from coding_agent.remote.client import get_remote, get_remote_session

    endpoint = get_remote(name)
    session = get_remote_session(endpoint, session_id)
    _print_mapping(session)


@remote.command("session")
@click.argument("name")
@click.argument("session_id")
def remote_session(name: str, session_id: str) -> None:
    """Show one remote session."""
    from coding_agent.remote.client import get_remote, get_remote_session

    endpoint = get_remote(name)
    _print_mapping(get_remote_session(endpoint, session_id))


@remote.command("runs")
@click.argument("name")
@click.option("--session", "session_id", required=True, help="Remote session ID.")
def remote_runs(name: str, session_id: str) -> None:
    """List durable runs for a remote session."""
    from coding_agent.remote.client import get_remote, list_remote_session_runs

    endpoint = get_remote(name)
    runs = list_remote_session_runs(endpoint, session_id)
    if not runs:
        click.echo("No remote runs found.")
        return
    for run in runs:
        metadata = run.get("metadata")
        executor_id = (
            metadata.get("executor_id") or metadata.get("worker_id")
            if isinstance(metadata, dict)
            else ""
        )
        click.echo(
            "\t".join(
                [
                    str(run.get("run_id", "")),
                    str(run.get("status", "")),
                    str(executor_id),
                    str(run.get("tape_id", "")),
                ]
            )
        )


@remote.command("run-info")
@click.argument("name")
@click.argument("run_id")
def remote_run_info(name: str, run_id: str) -> None:
    """Show one durable remote run."""
    from coding_agent.remote.client import get_remote, get_remote_run

    endpoint = get_remote(name)
    _print_mapping(get_remote_run(endpoint, run_id))


@remote.command("events")
@click.argument("name")
@click.option("--run", "run_id", required=True, help="Remote run ID.")
def remote_events(name: str, run_id: str) -> None:
    """List replayed display events for a remote run."""
    from coding_agent.remote.client import get_remote, list_remote_run_display_events

    endpoint = get_remote(name)
    events = list_remote_run_display_events(endpoint, run_id)
    if not events:
        click.echo("No remote events found.")
        return
    for event in events:
        click.echo(
            "\t".join(
                [
                    str(event.get("sequence", "")),
                    str(event.get("display_kind", "")),
                    str(event.get("source_event_id", "")),
                    str(event.get("created_at", "")),
                ]
            )
        )


@remote.command("interactions")
@click.argument("name")
@click.option("--session", "session_id", help="Filter by remote session ID.")
@click.option("--run", "run_id", help="Filter by remote run ID.")
@click.option("--status", "status", help="Filter by interaction status.")
def remote_interactions(
    name: str,
    session_id: str | None,
    run_id: str | None,
    status: str | None,
) -> None:
    """List durable remote runtime interactions."""
    from coding_agent.remote.client import get_remote, list_remote_interactions

    endpoint = get_remote(name)
    interactions = list_remote_interactions(
        endpoint,
        session_id=session_id,
        run_id=run_id,
        status=status,
    )
    if not interactions:
        click.echo("No remote interactions found.")
        return
    for interaction in interactions:
        metadata = interaction.get("metadata")
        request_id = (
            metadata.get("request_id")
            if isinstance(metadata, dict)
            and isinstance(metadata.get("request_id"), str)
            else ""
        )
        click.echo(
            "\t".join(
                [
                    str(interaction.get("interaction_id", "")),
                    str(interaction.get("status", "")),
                    str(interaction.get("interaction_kind", "")),
                    request_id,
                    str(interaction.get("run_id", "")),
                    str(interaction.get("created_at", "")),
                ]
            )
        )


@remote.command("interaction")
@click.argument("name")
@click.argument("interaction_id")
def remote_interaction(name: str, interaction_id: str) -> None:
    """Show one durable remote runtime interaction."""
    from coding_agent.remote.client import get_remote, get_remote_interaction

    endpoint = get_remote(name)
    _print_mapping(get_remote_interaction(endpoint, interaction_id))


@remote.command("resolve-interaction")
@click.argument("name")
@click.argument("interaction_id")
@click.option("--approve", "approve", is_flag=True, help="Approve the interaction.")
@click.option("--reject", "reject", is_flag=True, help="Reject the interaction.")
@click.option("--feedback", "feedback", help="Optional approval feedback.")
@click.option(
    "--scope",
    "scope",
    type=click.Choice(["once", "session", "always"]),
    default="once",
    show_default=True,
)
def remote_resolve_interaction(
    name: str,
    interaction_id: str,
    approve: bool,
    reject: bool,
    feedback: str | None,
    scope: str,
) -> None:
    """Resolve a pending approval interaction."""
    from coding_agent.remote.client import get_remote, resolve_remote_interaction

    if approve == reject:
        raise click.ClickException("Pass exactly one of --approve or --reject.")
    endpoint = get_remote(name)
    _print_mapping(
        resolve_remote_interaction(
            endpoint,
            interaction_id,
            approved=approve,
            feedback=feedback,
            scope=scope,
        )
    )


@remote.command("workers")
@click.argument("name")
def remote_workers(name: str) -> None:
    """Compatibility alias for `remote executors`."""
    _print_remote_executors(name, empty_message="No remote workers found.")


@remote.command("executors")
@click.argument("name")
def remote_executors(name: str) -> None:
    """List attached executor health derived from durable runs."""
    _print_remote_executors(name, empty_message="No remote executors found.")


def _print_remote_executors(name: str, *, empty_message: str) -> None:
    from coding_agent.remote.client import get_remote, list_remote_executors

    endpoint = get_remote(name)
    executors = list_remote_executors(endpoint)
    if not executors:
        click.echo(empty_message)
        return
    for executor in executors:
        click.echo(
            "\t".join(
                [
                    str(executor.get("executor_id") or executor.get("worker_id", "")),
                    str(executor.get("status", "")),
                    str(executor.get("executor_kind", "")),
                    str(executor.get("current_run_id", "")),
                    str(executor.get("last_seen_at", "")),
                ]
            )
        )


@remote.command("worker-status")
@click.argument("name")
@click.argument("worker_id")
def remote_worker_status(name: str, worker_id: str) -> None:
    """Compatibility alias for `remote executor-status`."""
    _print_remote_executor_status(name, worker_id)


@remote.command("executor-status")
@click.argument("name")
@click.argument("executor_id")
def remote_executor_status(name: str, executor_id: str) -> None:
    """Show one attached executor status."""
    _print_remote_executor_status(name, executor_id)


def _print_remote_executor_status(name: str, executor_id: str) -> None:
    from coding_agent.remote.client import get_remote, get_remote_executor

    endpoint = get_remote(name)
    _print_mapping(get_remote_executor(endpoint, executor_id))


@remote.command("prompt")
@click.argument("name")
@click.argument("session_id")
@click.option("--goal", required=True, help="Prompt to send to the remote session.")
def remote_prompt(name: str, session_id: str, goal: str) -> None:
    """Send a prompt to an existing remote session."""
    from coding_agent.remote.client import (
        auth_headers,
        get_remote,
        stream_prompt_or_run_request,
    )

    endpoint = get_remote(name)
    status = stream_prompt_or_run_request(
        base_url=endpoint.url,
        session_id=session_id,
        prompt=goal,
        headers=auth_headers(endpoint),
    )
    raise SystemExit(status)


@remote.command("resume")
@click.argument("name")
@click.option("--session", "session_id", required=True, help="Session id to resume.")
@click.option("--prompt", default=None, help="Optional resume instruction.")
def remote_resume(name: str, session_id: str, prompt: str | None) -> None:
    """Resume an existing remote session from durable context."""
    from coding_agent.remote.client import (
        auth_headers,
        get_remote,
        stream_resume_or_run_request,
    )

    endpoint = get_remote(name)
    status = stream_resume_or_run_request(
        base_url=endpoint.url,
        session_id=session_id,
        prompt=prompt,
        headers=auth_headers(endpoint),
    )
    raise SystemExit(status)


@remote.command("attach")
@click.argument("name")
@click.argument("session_id")
def remote_attach(name: str, session_id: str) -> None:
    """Attach to a remote session event stream."""
    from coding_agent.remote.client import (
        attach_remote_session,
        auth_headers,
        get_remote,
    )

    endpoint = get_remote(name)
    status = attach_remote_session(
        base_url=endpoint.url,
        session_id=session_id,
        headers=auth_headers(endpoint),
    )
    raise SystemExit(status)


@remote_sessions.command("cancel")
@click.argument("name")
@click.argument("session_id")
def remote_sessions_cancel(name: str, session_id: str) -> None:
    """Cancel the active turn for a remote session."""
    from coding_agent.remote.client import cancel_remote_session, get_remote

    endpoint = get_remote(name)
    result = cancel_remote_session(endpoint, session_id)
    turn_id = result.get("turn_id")
    if isinstance(turn_id, str) and turn_id:
        click.echo(f"Cancelling remote session {session_id} turn {turn_id}")
        return
    click.echo(f"Cancelling remote session {session_id}")


@remote_sessions.command("close")
@click.argument("name")
@click.argument("session_id")
def remote_sessions_close(name: str, session_id: str) -> None:
    """Close a remote session."""
    from coding_agent.remote.client import (
        auth_headers,
        delete_remote_session,
        get_remote,
    )

    endpoint = get_remote(name)
    delete_remote_session(
        base_url=endpoint.url,
        session_id=session_id,
        headers=auth_headers(endpoint),
    )
    click.echo(f"Closed remote session {session_id}")


@remote.group("workspaces")
def remote_workspaces() -> None:
    """Inspect and clean remote workspaces."""


@remote_workspaces.command("list")
@click.argument("name")
def remote_workspaces_list(name: str) -> None:
    """List remote workspaces."""
    from coding_agent.remote.client import get_remote, list_remote_workspaces

    endpoint = get_remote(name)
    workspaces = list_remote_workspaces(endpoint)
    if not workspaces:
        click.echo("No remote workspaces found.")
        return
    for workspace in workspaces:
        click.echo(
            "\t".join(
                [
                    str(workspace.get("workspace_id", "")),
                    str(workspace.get("status", "")),
                    str(workspace.get("updated_at", "")),
                ]
            )
        )


@remote_workspaces.command("status")
@click.argument("name")
@click.argument("workspace_id")
def remote_workspaces_status(name: str, workspace_id: str) -> None:
    """Show one remote workspace."""
    from coding_agent.remote.client import get_remote, get_remote_workspace

    endpoint = get_remote(name)
    workspace = get_remote_workspace(endpoint, workspace_id)
    for key in [
        "workspace_id",
        "status",
        "session_id",
        "provider",
        "provider_instance_id",
        "workspace_host_label",
        "retention_policy",
        "expires_at",
        "is_local",
        "updated_at",
        "cleanup_error",
    ]:
        value = workspace.get(key)
        if value is not None:
            click.echo(f"{key}: {value}")


@remote_workspaces.command("retain")
@click.argument("name")
@click.argument("workspace_id")
@click.option(
    "--policy",
    "retention_policy",
    default="ttl",
    type=click.Choice(["delete_on_close", "ttl", "pinned", "manual"]),
    show_default=True,
)
@click.option("--ttl", "ttl_seconds", type=int, default=None, help="TTL in seconds.")
def remote_workspaces_retain(
    name: str,
    workspace_id: str,
    retention_policy: str,
    ttl_seconds: int | None,
) -> None:
    """Update remote workspace retention policy."""
    from coding_agent.remote.client import get_remote, retain_remote_workspace

    endpoint = get_remote(name)
    result = retain_remote_workspace(
        endpoint,
        workspace_id,
        retention_policy=retention_policy,
        ttl_seconds=ttl_seconds,
    )
    click.echo(
        "Workspace "
        f"{result.get('workspace_id', workspace_id)} retained as "
        f"{result.get('retention_policy', retention_policy)}"
    )


@remote_workspaces.command("pin")
@click.argument("name")
@click.argument("workspace_id")
def remote_workspaces_pin(name: str, workspace_id: str) -> None:
    """Pin a remote workspace."""
    from coding_agent.remote.client import get_remote, pin_remote_workspace

    endpoint = get_remote(name)
    result = pin_remote_workspace(endpoint, workspace_id)
    click.echo(f"Workspace {result.get('workspace_id', workspace_id)} pinned")


@remote_workspaces.command("unpin")
@click.argument("name")
@click.argument("workspace_id")
@click.option(
    "--policy",
    "retention_policy",
    default=None,
    type=click.Choice(["delete_on_close", "ttl"]),
    help="Policy to apply after unpinning.",
)
@click.option("--ttl", "ttl_seconds", type=int, default=None, help="TTL in seconds.")
def remote_workspaces_unpin(
    name: str,
    workspace_id: str,
    retention_policy: str | None,
    ttl_seconds: int | None,
) -> None:
    """Unpin a remote workspace."""
    from coding_agent.remote.client import get_remote, unpin_remote_workspace

    endpoint = get_remote(name)
    result = unpin_remote_workspace(
        endpoint,
        workspace_id,
        retention_policy=retention_policy,
        ttl_seconds=ttl_seconds,
    )
    click.echo(
        "Workspace "
        f"{result.get('workspace_id', workspace_id)} unpinned to "
        f"{result.get('retention_policy', retention_policy or 'default policy')}"
    )


@remote_workspaces.command("cleanup")
@click.argument("name")
@click.option("--stale", is_flag=True, help="Run server-side stale workspace GC.")
def remote_workspaces_cleanup(name: str, stale: bool) -> None:
    """Clean stale remote workspaces."""
    from coding_agent.remote.client import cleanup_stale_remote_workspaces, get_remote

    if not stale:
        raise click.ClickException("Pass --stale to run stale workspace cleanup.")
    endpoint = get_remote(name)
    cleaned_count = cleanup_stale_remote_workspaces(endpoint)
    click.echo(f"Cleaned {cleaned_count} stale workspaces")


@remote_workspaces.command("rm")
@click.argument("name")
@click.argument("workspace_id")
def remote_workspaces_rm(name: str, workspace_id: str) -> None:
    """Clean one remote workspace by id."""
    from coding_agent.remote.client import cleanup_remote_workspace, get_remote

    endpoint = get_remote(name)
    _ = cleanup_remote_workspace(endpoint, workspace_id)
    click.echo(f"Cleaned workspace {workspace_id}")


@remote.command("result")
@click.argument("name")
@click.option("--session", "session_id", required=True, help="Remote session ID")
def remote_result(name: str, session_id: str) -> None:
    """Show the result summary for a remote session."""
    from coding_agent.remote.client import get_remote, get_remote_session_result

    endpoint = get_remote(name)
    result = get_remote_session_result(endpoint, session_id)
    _print_remote_session_result(result)


def _print_remote_session_result(result: dict[str, object]) -> None:
    session_id = result.get("session_id")
    status = result.get("status")
    turn_status = result.get("turn_status")
    workspace_id = result.get("workspace_id")
    provider_name = result.get("provider_name")
    model_name = result.get("model_name")
    final_answer = result.get("final_answer")
    verification_summary = result.get("verification_summary")
    failure_details = result.get("failure_details")

    if not isinstance(session_id, str) or not session_id:
        raise click.ClickException("Remote result response missing session_id")
    if not isinstance(status, str) or not status:
        raise click.ClickException("Remote result response missing status")
    if not isinstance(turn_status, str) or not turn_status:
        raise click.ClickException("Remote result response missing turn_status")

    click.echo(f"Session: {session_id}")
    click.echo(f"Status: {status}")
    click.echo(f"Turn: {turn_status}")
    if isinstance(workspace_id, str) and workspace_id:
        click.echo(f"Workspace: {workspace_id}")
    if isinstance(provider_name, str) and provider_name:
        click.echo(f"Provider: {provider_name}")
    if isinstance(model_name, str) and model_name:
        click.echo(f"Model: {model_name}")
    if isinstance(final_answer, str) and final_answer:
        click.echo()
        click.echo("Final answer:")
        click.echo(final_answer)
    if isinstance(verification_summary, str) and verification_summary:
        click.echo()
        click.echo("Verification:")
        click.echo(verification_summary)
    if isinstance(failure_details, str) and failure_details:
        click.echo()
        click.echo("Failure:")
        click.echo(failure_details)


@remote.command("diff")
@click.argument("name")
@click.option("--session", "session_id", required=True, help="Remote session ID")
def remote_diff(name: str, session_id: str) -> None:
    """Show changed files for a remote session workspace."""
    from coding_agent.remote.client import download_workspace_diff, get_remote

    endpoint = get_remote(name)
    try:
        diff = download_workspace_diff(endpoint, session_id)
    except click.ClickException as exc:
        _raise_snapshot_workspace_result_guidance(
            exc,
            remote_name=name,
            session_id=session_id,
            command_name="diff",
        )
    files = diff.get("files")
    if not isinstance(files, list):
        raise click.ClickException("Remote workspace diff response missing files")
    workspace_id = diff.get("workspace_id")
    workspace_label = workspace_id if isinstance(workspace_id, str) else "unknown"
    additions = diff.get("additions")
    deletions = diff.get("deletions")
    if not isinstance(additions, int) or not isinstance(deletions, int):
        raise click.ClickException("Remote workspace diff response missing totals")

    click.echo(
        f"Remote workspace {workspace_label}: {len(files)} files changed, +{additions}/-{deletions}"
    )
    for raw_file in files:
        if not isinstance(raw_file, dict):
            raise click.ClickException("Remote workspace diff file entry is invalid")
        path = raw_file.get("path")
        status = raw_file.get("status")
        if not isinstance(path, str) or not isinstance(status, str):
            raise click.ClickException("Remote workspace diff file entry is invalid")
        old_path = raw_file.get("old_path")
        display_path = (
            f"{old_path} -> {path}" if isinstance(old_path, str) and old_path else path
        )
        if raw_file.get("binary") is True:
            change_summary = "binary"
        else:
            file_additions = raw_file.get("additions")
            file_deletions = raw_file.get("deletions")
            if isinstance(file_additions, int) and isinstance(file_deletions, int):
                change_summary = f"+{file_additions}/-{file_deletions}"
            else:
                change_summary = "+?/-?"
        click.echo(f"{status.ljust(9)} {display_path}  {change_summary}")


@remote.command("patch")
@click.argument("name")
@click.option("--session", "session_id", required=True, help="Remote session ID")
def remote_patch(name: str, session_id: str) -> None:
    """Print a unified patch for a remote session workspace."""
    from coding_agent.remote.client import download_workspace_patch, get_remote

    endpoint = get_remote(name)
    try:
        patch = download_workspace_patch(endpoint, session_id)
    except click.ClickException as exc:
        _raise_snapshot_workspace_result_guidance(
            exc,
            remote_name=name,
            session_id=session_id,
            command_name="patch",
        )
    click.echo(patch, nl=False)


def _raise_snapshot_workspace_result_guidance(
    exc: click.ClickException,
    *,
    remote_name: str,
    session_id: str,
    command_name: str,
) -> NoReturn:
    message = exc.format_message()
    if "requires a Git workspace" not in message:
        raise exc
    raise click.ClickException(
        message
        + "\n"
        + f"snapshot fallback sessions do not support remote {command_name}. "
        + "Use a Git-backed remote run for diff/patch/publish, or inspect this "
        + "snapshot result with:\n"
        + f"  Use 'coding_agent remote result {remote_name} --session {session_id}'\n"
        + f"  Use 'coding_agent remote download {remote_name} --session {session_id}'"
    ) from exc


@remote.command("publish")
@click.argument("name")
@click.option("--session", "session_id", required=True, help="Remote session ID")
@click.option(
    "--branch",
    "branch_name",
    help="Publish the remote session changes to this Git branch.",
)
@click.option(
    "--pr",
    "publish_mode",
    flag_value="pr",
    help="Create a GitHub pull request after publishing the branch.",
)
@click.option(
    "--branch-only",
    "publish_mode",
    flag_value="branch",
    default="branch",
    help="Publish the remote session changes as a Git branch only.",
)
def remote_publish(
    name: str,
    session_id: str,
    branch_name: str | None,
    publish_mode: str,
) -> None:
    """Publish remote session results as a Git branch or PR."""
    from coding_agent.remote.client import get_remote, publish_remote_result

    endpoint = get_remote(name)
    if branch_name is None:
        branch_name = f"coding-agent/session-{session_id}"
    publication = publish_remote_result(
        endpoint,
        session_id,
        mode=publish_mode,
        branch_name=branch_name,
    )
    published_branch = publication.get("branch_name")
    pushed_ref = publication.get("pushed_ref")
    commit_sha = publication.get("commit_sha")
    remote_url = publication.get("remote_url")
    pr_url = publication.get("pr_url")
    status = publication.get("status")
    if not isinstance(published_branch, str) or not published_branch:
        raise click.ClickException("Remote publish response missing branch_name")
    if not isinstance(commit_sha, str) or not commit_sha:
        raise click.ClickException("Remote publish response missing commit_sha")
    if not isinstance(remote_url, str) or not remote_url:
        raise click.ClickException("Remote publish response missing remote_url")
    if publish_mode == "pr":
        if isinstance(pr_url, str) and pr_url:
            click.echo(f"Published PR {pr_url}")
            click.echo(f"Branch: {published_branch}")
        elif status in {"partial", "unsupported", "failed"}:
            error = publication.get("error")
            if isinstance(error, str) and error:
                click.echo(error)
            click.echo(f"Branch: {published_branch}")
        else:
            raise click.ClickException("Remote PR publication did not complete")
    else:
        if status == "partial":
            error = publication.get("error")
            if isinstance(error, str) and error:
                click.echo(error)
            click.echo(f"Partial branch publication {published_branch}")
        elif status != "published":
            raise click.ClickException("Remote branch publication did not complete")
        else:
            click.echo(f"Published branch {published_branch}")
    if isinstance(pushed_ref, str) and pushed_ref:
        click.echo(f"Ref: {pushed_ref}")
    click.echo(f"Commit: {commit_sha}")
    click.echo(f"Remote: {remote_url}")
    if publish_mode == "pr" and status == "failed":
        raise click.ClickException("Remote PR publication did not complete")


@remote.command("download")
@click.argument("name")
@click.option("--session", "session_id", required=True, help="Remote session ID")
@click.option(
    "--repo",
    default=".",
    help="Local repo/workspace to overwrite with the remote snapshot.",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Download and overwrite the local workspace without confirmation.",
)
def remote_download(name: str, session_id: str, repo: str, yes: bool) -> None:
    """Download a remote session workspace snapshot."""
    from coding_agent.remote.client import auth_headers, get_remote

    endpoint = get_remote(name)
    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.is_dir():
        raise click.ClickException(f"--repo must be an existing directory: {repo}")
    _download_and_restore_workspace(
        base_url=endpoint.url,
        session_id=session_id,
        headers=auth_headers(endpoint),
        repo_path=repo_path,
        yes=yes,
    )


def _download_and_restore_workspace(
    *,
    base_url: str,
    session_id: str,
    headers: dict[str, str],
    repo_path: Path,
    yes: bool,
) -> None:
    from coding_agent.remote.client import (
        download_workspace_archive,
        download_workspace_manifest,
    )
    from coding_agent.workspace_archive import extract_workspace_archive_base64

    manifest = download_workspace_manifest(
        base_url=base_url,
        session_id=session_id,
        headers=headers,
    )
    changed_count = _manifest_file_count(manifest, "changed_files")
    deleted_count = _manifest_file_count(manifest, "deleted_files")
    total = manifest.get("total_bytes")
    if not isinstance(total, int):
        raise click.ClickException("Remote workspace manifest missing total_bytes")
    click.echo(
        f"Remote snapshot contains {changed_count} archived files, {deleted_count} deleted entries, {total} bytes"
    )
    click.echo(f"This will overwrite {repo_path} while preserving .git.")
    if not yes and not click.confirm("Continue?", default=False):
        raise click.ClickException("Remote workspace download cancelled.")
    archive_base64 = download_workspace_archive(
        base_url=base_url,
        session_id=session_id,
        headers=headers,
    )
    extract_workspace_archive_base64(repo_path, archive_base64)
    click.echo(
        f"Downloaded remote workspace snapshot and overwrote {repo_path} while preserving .git"
    )


def _manifest_file_count(manifest: dict[str, object], field: str) -> int:
    values = manifest.get(field)
    if not isinstance(values, list):
        raise click.ClickException(f"Remote workspace manifest missing {field}")
    values = cast(list[object], values)
    return len(values)


@click.command()
@click.argument("name")
@click.option("--session", "session_id", required=True, help="Remote session ID")
@click.option("--goal", required=True, help="Prompt to send to the remote session")
def attach(name: str, session_id: str, goal: str) -> None:
    """Send one prompt to an existing remote session."""
    from coding_agent.remote.client import (
        auth_headers,
        get_remote,
        stream_prompt_or_run_request,
    )

    endpoint = get_remote(name)
    raise SystemExit(
        stream_prompt_or_run_request(
            base_url=endpoint.url,
            session_id=session_id,
            prompt=goal,
            headers=auth_headers(endpoint),
        )
    )
