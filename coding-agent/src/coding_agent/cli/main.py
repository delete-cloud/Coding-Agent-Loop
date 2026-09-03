"""CLI entry point: python -m coding_agent"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import subprocess
import sys

import click

from coding_agent.core.app import create_agent, create_child_pipeline  # noqa: F401
from coding_agent.cli.acp_command import acp
from coding_agent.cli.kb_commands import kb
from coding_agent.cli.oauth_commands import oauth_cli
from coding_agent.cli.postmortem_commands import postmortem
from coding_agent.cli.remote_commands import attach, remote
from coding_agent.cli.serve_command import daemon, serve
from coding_agent.cli.stats_command import stats
from coding_agent.cli.verify_command import verify
from coding_agent.core.config import Config, load_config, validate_provider_value
from coding_agent.remote.approval import APPROVAL_POLICIES
from coding_agent.approval import ApprovalPolicy
from coding_agent.cli.local_runtime import (
    LocalCliSessionManager,
    create_local_cli_session_manager,
    local_cli_session_origin,
)
from coding_agent.stores.migration import migrate_legacy_storage_to_sqlite
from coding_agent.stores.local import local_sqlite_storage_config
from coding_agent.ui.headless import HeadlessConsumer
from coding_agent.ui.rich_tui import CodingAgentTUI
from coding_agent.wire.protocol import TurnEnd, WireMessage


def _validate_cli_provider(
    ctx: click.Context,
    param: click.Parameter,
    value: str | None,
) -> str | None:
    try:
        return validate_provider_value(value)
    except ValueError as exc:
        raise click.BadParameter(str(exc), ctx=ctx, param=param) from exc


REMOTE_APPROVAL_CHOICES = click.Choice(APPROVAL_POLICIES)


@dataclass(frozen=True)
class WorktreeSnapshot:
    status: str
    diff: str


def _collect_shared_cli_args(
    *,
    model: str | None,
    provider_name: str | None,
    base_url: str | None,
    api_key: str | None,
) -> dict[str, object]:
    cli_args: dict[str, object] = {}
    if provider_name is not None:
        cli_args["provider"] = provider_name
    if model is not None:
        cli_args["model"] = model
    if base_url is not None:
        cli_args["base_url"] = base_url
    if api_key is not None:
        cli_args["api_key"] = api_key
    return cli_args


def _get_shared_cli_args(ctx: click.Context) -> dict[str, object]:
    if isinstance(ctx.obj, dict):
        shared_args = ctx.obj.get("shared_cli_args")
        if isinstance(shared_args, dict):
            return dict(shared_args)
    return {}


def _build_runtime_config(
    ctx: click.Context, command_args: dict[str, object] | None = None
) -> Config:
    cli_args = _get_shared_cli_args(ctx)
    if command_args:
        for key, value in command_args.items():
            if value is not None:
                cli_args[key] = value
    return load_config(cli_args=cli_args or None)


@click.group(invoke_without_command=True)
@click.option("--model", default=None, help="Model name")
@click.option(
    "--provider",
    "provider_name",
    default=None,
    type=str,
    callback=_validate_cli_provider,
)
@click.option("--base-url", default=None, help="OpenAI-compatible API base URL")
@click.option("--api-key", default=None, help="API key")
@click.pass_context
def main(ctx, model, provider_name, base_url, api_key):
    """Coding Agent CLI.

    Without subcommand: starts interactive REPL mode (default)
    """
    ctx.ensure_object(dict)
    ctx.obj["shared_cli_args"] = _collect_shared_cli_args(
        model=model,
        provider_name=provider_name,
        base_url=base_url,
        api_key=api_key,
    )

    if ctx.invoked_subcommand is None:
        # Default to interactive REPL mode
        import asyncio
        from coding_agent.cli.repl import run_repl

        if not sys.stdout.isatty():
            raise click.UsageError(
                "interactive REPL mode requires an interactive terminal; use "
                "'python -m coding_agent repl' in a terminal, or "
                "'python -m coding_agent daemon run --goal \"<task>\"' with a "
                "running daemon for a daemon-backed local session"
            )

        config = _build_runtime_config(ctx)
        asyncio.run(run_repl(config))


@main.command()
@click.option("--goal", required=True, help="Task goal for the agent")
@click.option("--repo", default=".", help="Repository path")
@click.option("--max-steps", default=30, help="Max steps per turn")
@click.option("--approval", default="yolo", type=REMOTE_APPROVAL_CHOICES)
@click.option(
    "--parallel/--no-parallel", default=True, help="Enable parallel tool execution"
)
@click.option("--max-parallel", default=5, help="Maximum parallel tool executions")
@click.option("--cache/--no-cache", default=True, help="Enable tool result caching")
@click.option("--cache-size", default=100, help="Maximum cached entries")
@click.option(
    "--tui",
    is_flag=True,
    help="Use Rich TUI interface for the dev/testkit one-shot run",
)
@click.option(
    "--patch",
    "patch_mode",
    is_flag=True,
    hidden=True,
    help="Deprecated dev/testkit compatibility flag.",
)
@click.option(
    "--verify-cmd",
    "verify_commands",
    multiple=True,
    hidden=True,
    help="Deprecated dev/testkit compatibility flag. Repeat for multiple commands.",
)
@click.pass_context
def run(
    ctx,
    goal,
    repo,
    max_steps,
    approval,
    parallel,
    max_parallel,
    cache,
    cache_size,
    tui,
    patch_mode,
    verify_commands,
):
    """Run a dev/testkit one-shot local session (compatibility path)."""
    import asyncio

    config = _build_runtime_config(
        ctx,
        command_args={
            "repo": repo,
            "max_steps": max_steps,
            "approval_mode": approval,
            "enable_parallel_tools": parallel,
            "max_parallel_tools": max_parallel,
            "enable_cache": cache,
            "cache_size": cache_size,
        },
    )

    repo_root = Path(config.repo).expanduser().resolve()
    if patch_mode or verify_commands:
        _warn_deprecated_run_patch_flags()
    before_snapshot = _capture_worktree_snapshot(repo_root) if patch_mode else None
    effective_goal = (
        _patch_oriented_goal(goal, tuple(verify_commands)) if patch_mode else goal
    )

    if tui:
        asyncio.run(_run_with_tui(config, effective_goal))
    else:
        asyncio.run(_run_headless(config, effective_goal))

    if before_snapshot is not None:
        after_snapshot = _capture_worktree_snapshot(repo_root)
        _ensure_patch_run_changed_worktree(before_snapshot, after_snapshot)
    if verify_commands:
        _run_post_run_verification(repo_root, tuple(verify_commands))


def _warn_deprecated_run_patch_flags() -> None:
    click.echo(
        "Warning: coding_agent run --patch/--verify-cmd are deprecated "
        "dev/testkit compatibility flags; use REPL or daemon-backed sessions "
        "for product dogfood.",
        err=True,
    )


def _patch_oriented_goal(goal: str, verify_commands: tuple[str, ...]) -> str:
    lines = [
        goal,
        "",
        "Patch-oriented run contract:",
        "- Modify repository files when the task asks for code, tests, docs, or config changes.",
        "- Do not stop after planning; inspect the relevant files and produce a concrete patch.",
        "- Before finishing, inspect git status/diff and run focused validation.",
        "- If validation fails, fix the issue and rerun the same validation once.",
        "- Final response must summarize changed files and validation commands.",
    ]
    if verify_commands:
        lines.append("- Required validation commands:")
        lines.extend(f"  - {command}" for command in verify_commands)
    return "\n".join(lines)


def _capture_worktree_snapshot(repo_root: Path) -> WorktreeSnapshot:
    return WorktreeSnapshot(
        status=_git_capture(
            repo_root,
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        ),
        diff="\n".join(
            [
                _git_capture(
                    repo_root,
                    ["git", "diff", "--binary", "--no-ext-diff", "--"],
                ),
                _git_capture(
                    repo_root,
                    ["git", "diff", "--cached", "--binary", "--no-ext-diff", "--"],
                ),
            ]
        ),
    )


def _git_capture(repo_root: Path, command: list[str]) -> str:
    completed = subprocess.run(
        command,
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise click.ClickException(
            f"patch run requires a git worktree; {' '.join(command)} failed"
            + (f": {stderr}" if stderr else "")
        )
    return completed.stdout


def _ensure_patch_run_changed_worktree(
    before: WorktreeSnapshot, after: WorktreeSnapshot
) -> None:
    if before == after:
        raise click.ClickException(
            "patch run produced no repository changes; retry in REPL or use a more "
            "specific task packet"
        )


def _run_post_run_verification(repo_root: Path, commands: tuple[str, ...]) -> None:
    for command in commands:
        args = shlex.split(command)
        if not args:
            raise click.ClickException("--verify-cmd must not be empty")
        completed = subprocess.run(
            args,
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            output = (completed.stdout + completed.stderr).strip()
            raise click.ClickException(
                f"verification command failed ({completed.returncode}): {command}"
                + (f"\n{output}" if output else "")
            )


@main.command()
@click.option("--repo", default=".", help="Repository path")
@click.option("--max-steps", default=30, help="Max steps per turn")
@click.pass_context
def repl(ctx, repo, max_steps):
    """Start interactive REPL mode (explicit)."""
    import asyncio
    from coding_agent.cli.repl import run_repl

    config = _build_runtime_config(
        ctx,
        command_args={
            "repo": repo,
            "max_steps": max_steps,
            "approval_mode": "yolo",
        },
    )
    asyncio.run(run_repl(config))


@main.command()
@click.option("--session", "session_id", default=None, help="Session id to resume.")
@click.option("--last", "resume_last", is_flag=True, help="Resume latest session.")
@click.option("--prompt", default=None, help="Optional resume instruction.")
@click.pass_context
def resume(ctx, session_id, resume_last, prompt):
    """Resume a managed local session from durable context."""
    import asyncio

    config = _build_runtime_config(ctx)
    asyncio.run(_resume_managed_session(config, session_id, resume_last, prompt))


@main.group("sessions")
def local_sessions() -> None:
    """Inspect local managed sessions."""


@local_sessions.command("list")
def local_sessions_list() -> None:
    """List local managed sessions."""
    asyncio.run(_print_local_sessions())


@local_sessions.command("status")
@click.argument("session_id")
def local_sessions_status(session_id: str) -> None:
    """Show one local managed session."""
    asyncio.run(_print_local_session(session_id))


@local_sessions.command("checkpoints")
@click.argument("session_id")
def local_sessions_checkpoints(session_id: str) -> None:
    """List checkpoints for a local managed session."""
    asyncio.run(_print_local_session_checkpoints(session_id))


@main.command("session")
@click.argument("session_id")
def local_session(session_id: str) -> None:
    """Show one local managed session."""
    asyncio.run(_print_local_session(session_id))


@main.group("storage")
def storage() -> None:
    """Manage local storage backends."""


@storage.command("migrate-sqlite")
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path, file_okay=False, dir_okay=True),
    default=None,
    help="Data directory containing legacy tapes/checkpoints.",
)
@click.option(
    "--tapes-dir",
    type=click.Path(path_type=Path, file_okay=False, dir_okay=True),
    default=None,
    help="Legacy JSONL tape directory. Defaults to DATA_DIR/tapes.",
)
@click.option(
    "--checkpoints-dir",
    type=click.Path(path_type=Path, file_okay=False, dir_okay=True),
    default=None,
    help="Legacy FS checkpoint directory. Defaults to DATA_DIR/checkpoints.",
)
@click.option(
    "--tape-sqlite",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="SQLite tape database path. Defaults to DATA_DIR/local.sqlite3.",
)
@click.option(
    "--checkpoint-sqlite",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="SQLite checkpoint database path. Defaults to DATA_DIR/local.sqlite3.",
)
@click.option(
    "--replace-tapes",
    is_flag=True,
    help="Replace existing SQLite tape rows when source JSONL differs.",
)
@click.option("--dry-run", is_flag=True, help="Report work without writing SQLite.")
def storage_migrate_sqlite(
    data_dir: Path | None,
    tapes_dir: Path | None,
    checkpoints_dir: Path | None,
    tape_sqlite: Path | None,
    checkpoint_sqlite: Path | None,
    replace_tapes: bool,
    dry_run: bool,
) -> None:
    """Migrate legacy JSONL tape and FS checkpoint storage to SQLite."""
    resolved_data_dir = data_dir or Path(os.environ.get("AGENT_DATA_DIR", "./data"))
    report = asyncio.run(
        migrate_legacy_storage_to_sqlite(
            resolved_data_dir,
            tapes_dir=tapes_dir,
            checkpoints_dir=checkpoints_dir,
            tape_sqlite_path=tape_sqlite,
            checkpoint_sqlite_path=checkpoint_sqlite,
            replace_tapes=replace_tapes,
            dry_run=dry_run,
        )
    )
    click.echo(
        "tapes: "
        f"scanned={report.tapes.scanned} "
        f"migrated={report.tapes.migrated} "
        f"skipped={report.tapes.skipped}"
    )
    click.echo(
        "checkpoints: "
        f"scanned={report.checkpoints.scanned} "
        f"migrated={report.checkpoints.migrated} "
        f"skipped={report.checkpoints.skipped}"
    )


async def _run_with_tui(config, goal):
    """Run a dev/testkit one-shot local session with TUI display."""
    tui = CodingAgentTUI(model_name=config.model, max_steps=config.max_steps)
    with tui:
        tui.add_user_message(goal)
        await _run_managed_one_shot(config, goal, tui.consumer)


async def _run_headless(config, goal):
    """Run a dev/testkit one-shot local session in headless mode."""
    consumer = HeadlessConsumer(auto_approve=config.approval_mode == "yolo")
    await _run_managed_one_shot(config, goal, consumer)


async def _resume_managed_session(
    config: Config,
    session_id: str | None,
    resume_last: bool,
    prompt: str | None,
) -> None:
    if session_id is not None and resume_last:
        raise click.UsageError("Pass either --session or --last, not both.")
    session_manager = _local_session_manager()
    try:
        resolved_session_id = session_id
        if resolved_session_id is None:
            if not resume_last:
                raise click.UsageError("Pass --session <id> or --last.")
            resolved_session_id = await _latest_managed_session_id(session_manager)
        session = await session_manager.get_session_async(resolved_session_id)
        await session_manager.acquire_session_owner(resolved_session_id)
        await session_manager.start_owner_lease_renewal()
        consumer = HeadlessConsumer(auto_approve=config.approval_mode == "yolo")
        task = asyncio.create_task(
            session_manager.resume_session(
                resolved_session_id,
                prompt=prompt,
                resume_reason="local_cli_resume",
            )
        )
        await _stream_managed_session_wire(session.wire, task, consumer)
        await task
    finally:
        await session_manager.close()


async def _latest_managed_session_id(session_manager: LocalCliSessionManager) -> str:
    session_ids = await session_manager.list_sessions_async()
    if not session_ids:
        raise click.ClickException("No local sessions found.")
    sessions = [
        await session_manager.get_session_async(session_id)
        for session_id in session_ids
    ]
    latest = max(sessions, key=lambda session: (session.last_activity, session.id))
    return latest.id


async def _print_local_sessions() -> None:
    session_manager = _local_session_manager()
    try:
        summaries = await _local_session_summaries(session_manager)
    finally:
        await session_manager.close()
    if not summaries:
        click.echo("No local sessions found.")
        return
    for summary in summaries:
        click.echo(
            "\t".join(
                [
                    str(summary.get("session_id", "")),
                    str(summary.get("status", "")),
                    str(summary.get("turn_status", "")),
                    str(summary.get("workspace_id", "")),
                    str(summary.get("last_run_status", "")),
                    "resumable" if summary.get("resumable") is True else "",
                    str(summary.get("last_interrupted_run_id", "")),
                    str(summary.get("latest_checkpoint_id", "")),
                ]
            )
        )


async def _print_local_session(session_id: str) -> None:
    session_manager = _local_session_manager()
    try:
        session = await session_manager.get_session_async(session_id)
        summary = await _local_session_summary(session_manager, session)
    finally:
        await session_manager.close()
    for key in sorted(summary):
        value = summary[key]
        click.echo(f"{key}: {value}")


async def _print_local_session_checkpoints(session_id: str) -> None:
    session_manager = _local_session_manager()
    try:
        checkpoints = await session_manager.list_checkpoints(session_id)
    finally:
        await session_manager.close()
    if not checkpoints:
        click.echo("No checkpoints found.")
        return
    for checkpoint in sorted(
        checkpoints,
        key=lambda item: (item.created_at, item.checkpoint_id),
        reverse=True,
    ):
        click.echo(
            "\t".join(
                [
                    checkpoint.checkpoint_id,
                    checkpoint.created_at.isoformat(),
                    str(checkpoint.entry_count),
                    str(checkpoint.window_start),
                    checkpoint.label or "",
                ]
            )
        )


async def _local_session_summaries(
    session_manager: LocalCliSessionManager,
) -> list[dict[str, object]]:
    sessions = [
        await session_manager.get_session_async(session_id)
        for session_id in await session_manager.list_sessions_async()
    ]
    summaries = [
        await _local_session_summary(session_manager, session) for session in sessions
    ]
    return sorted(
        summaries,
        key=lambda summary: (
            str(summary.get("last_activity", "")),
            str(summary.get("session_id", "")),
        ),
        reverse=True,
    )


async def _local_session_summary(
    session_manager: LocalCliSessionManager,
    session,
) -> dict[str, object]:
    summary = dict(session.as_dict())
    summary.update(await session_manager.session_resume_metadata(session.id))
    return summary


async def _run_managed_one_shot(config: Config, goal: str, consumer) -> None:
    """Execute a compatibility one-shot session through the local CLI runtime."""
    session_manager = _local_session_manager()
    try:
        session_id = await session_manager.create_session(
            repo_path=Path(config.repo),
            origin=local_cli_session_origin(
                entrypoint="run",
                mode="inline_testkit",
            ),
            approval_policy=_approval_policy_from_config(config.approval_mode),
            provider_name=config.provider,
            model_name=config.model,
            base_url=config.base_url,
            max_steps=config.max_steps,
        )
        await session_manager.start_owner_lease_renewal()
        session = await session_manager.get_session_async(session_id)
        task = asyncio.create_task(session_manager.run_agent(session_id, goal))
        await _stream_managed_session_wire(session.wire, task, consumer)
        await task
    finally:
        await session_manager.close()


async def _stream_managed_session_wire(
    wire, task: asyncio.Task[object], consumer
) -> None:
    while True:
        if task.done() and task.exception() is not None:
            await task
        message_task = asyncio.create_task(wire.get_next_outgoing())
        done, pending = await asyncio.wait(
            {message_task, task},
            timeout=1.0 if task.done() else None,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if message_task in done:
            message = message_task.result()
            await consumer.emit(message)
            if _is_root_turn_end(message):
                break
            continue
        message_task.cancel()
        try:
            await message_task
        except asyncio.CancelledError:
            pass
        if task in done:
            await task
            continue
        if task.done():
            break


def _is_root_turn_end(message: WireMessage) -> bool:
    return isinstance(message, TurnEnd) and not message.agent_id


def _approval_policy_from_config(value: str) -> ApprovalPolicy:
    if value == "yolo":
        return ApprovalPolicy.YOLO
    if value == "interactive":
        return ApprovalPolicy.INTERACTIVE
    if value == "auto":
        return ApprovalPolicy.AUTO
    raise ValueError(f"unsupported approval mode: {value}")


def _local_session_manager() -> LocalCliSessionManager:
    return create_local_cli_session_manager(
        storage_config=local_sqlite_storage_config()
    )


def _create_provider(config):
    """Create the appropriate provider based on config."""
    if config.provider == "anthropic":
        from coding_agent.providers.anthropic import AnthropicProvider

        return AnthropicProvider(
            model=config.model,
            api_key=config.api_key,
        )
    else:
        from coding_agent.providers.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
        )


main.add_command(kb)
main.add_command(oauth_cli)
main.add_command(postmortem)
main.add_command(stats)
main.add_command(daemon)
main.add_command(serve)
main.add_command(remote)
main.add_command(attach)
main.add_command(verify)
main.add_command(acp)


if __name__ == "__main__":
    main()
