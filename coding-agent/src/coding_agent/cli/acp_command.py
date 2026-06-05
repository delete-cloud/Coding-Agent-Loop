from __future__ import annotations

import asyncio
import os
from pathlib import Path
import time
import uuid

import click

from agentkit.tracing import configure_tracing
from coding_agent.acp import AcpServer, run_stdio
from coding_agent.approval import ApprovalPolicy
from coding_agent.cli.local_runtime import create_local_cli_session_manager
from coding_agent.server.stores.session_owner_store import SQLiteSessionOwnerStore


def _shared_cli_arg(ctx: click.Context, name: str) -> str | None:
    root_obj = ctx.find_root().obj
    if not isinstance(root_obj, dict):
        return None
    shared_args = root_obj.get("shared_cli_args")
    if not isinstance(shared_args, dict):
        return None
    value = shared_args.get(name)
    return value if isinstance(value, str) and value else None


@click.command("acp")
@click.option(
    "--approval",
    "approval_policy",
    default="auto",
    type=click.Choice(["auto", "yolo"]),
    help="Tool approval policy for ACP sessions.",
)
@click.option("--max-steps", default=30, type=int, show_default=True)
def acp(approval_policy: str, max_steps: int) -> None:
    """Run Coding Agent as an ACP stdio agent."""

    asyncio.run(_run_acp_stdio(click.get_current_context(), approval_policy, max_steps))


async def _run_acp_stdio(
    ctx: click.Context,
    approval_policy: str,
    max_steps: int,
) -> None:
    configure_tracing(enabled=False)
    data_dir = Path(os.environ.get("AGENT_DATA_DIR", "./data"))
    manager = create_local_cli_session_manager(
        storage_config={
            "http_session_backend": "fs",
            "tape_backend": "sqlite",
            "checkpoint_backend": "sqlite",
            "runtime_backend": "sqlite",
        },
        owner_store=SQLiteSessionOwnerStore(data_dir / "session_owners.sqlite3"),
        owner_id=f"acp-stdio:{uuid.uuid4().hex}",
        fencing_token=time.time_ns(),
    )
    try:
        server = AcpServer(
            manager,
            approval_policy=ApprovalPolicy(approval_policy),
            provider_name=_shared_cli_arg(ctx, "provider"),
            model_name=_shared_cli_arg(ctx, "model"),
            base_url=_shared_cli_arg(ctx, "base_url"),
            max_steps=max_steps,
        )
        await run_stdio(server)
    finally:
        await manager.close()


__all__ = ["acp"]
