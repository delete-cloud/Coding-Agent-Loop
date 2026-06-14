from __future__ import annotations

import asyncio
import json
import time
import uuid

import click

from agentkit.tracing import configure_tracing
from coding_agent.acp import AcpServer, AcpMode, run_stdio
from coding_agent.approval import ApprovalPolicy
from coding_agent.cli.local_runtime import create_local_cli_session_manager
from coding_agent.stores.local import (
    local_data_dir,
    local_sqlite_path,
    local_sqlite_storage_config,
)
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


def _parse_acp_mode(mode_str: str) -> AcpMode:
    """Parse a mode string in format 'id:name[:provider[:model[:base_url]]]'.

    When the 5th field matches thinking on/off tokens, it is treated as a
    thinking override rather than a base_url.  e.g.  'ds:DeepSeek:deepseek:ds-chat::on:high'
    (empty base_url before the thinking flag).
    """
    if not mode_str:
        raise click.BadParameter("--acp-mode value must not be empty")
    parts = mode_str.split(":", 6)
    mode_id = parts[0]
    if not mode_id:
        raise click.BadParameter(
            f"--acp-mode '{mode_str}' has empty mode id"
        )
    name = parts[1] if len(parts) > 1 and parts[1] else mode_id
    provider = parts[2] if len(parts) > 2 and parts[2] else None
    model = parts[3] if len(parts) > 3 and parts[3] else None

    # Distinguish base_url from thinking shorthand by scanning for the first
    # field that matches thinking on/off tokens. Everything before that is
    # conventional fields; everything from that point is thinking config.
    THINKING_TOKENS = frozenset({"on", "off", "true", "false", "0", "1"})
    base_url: str | None = None
    thinking: dict[str, Any] | None = None

    # Try parts[4] as base_url first. If parts[5] exists and looks like
    # a thinking token, treat parts[4] as a possibly-empty base_url and
    # parts[5:] as thinking shorthand.
    if len(parts) > 5 and parts[5].lower() in THINKING_TOKENS:
        base_url = parts[4] if len(parts) > 4 and parts[4] else None
        thinking = _parse_thinking_fields(parts[5].lower(), parts[6:])
    elif len(parts) > 4 and parts[4] and parts[4].lower() in THINKING_TOKENS:
        # parts[4] is a thinking token, not base_url
        thinking = _parse_thinking_fields(parts[4].lower(), parts[5:])
    elif len(parts) > 4 and parts[4]:
        base_url = parts[4]

    return AcpMode(
        id=mode_id,
        name=name,
        description=f"Provider: {provider or 'any'}, Model: {model or 'any'}",
        provider=provider,
        model=model,
        base_url=base_url,
        thinking=thinking,
    )


def _parse_thinking_fields(enabled_str: str, rest: list[str]) -> dict[str, Any]:
    thinking: dict[str, Any] = {}
    if enabled_str in ("on", "true", "1"):
        thinking["enabled"] = True
    else:
        thinking["enabled"] = False
    if rest:
        raw_effort = rest[0].lower()
        if raw_effort in ("low", "medium", "high"):
            thinking["effort"] = raw_effort
    return thinking


@click.command("acp")
@click.option(
    "--approval",
    "approval_policy",
    default="auto",
    type=click.Choice(["auto", "yolo"]),
    help="Tool approval policy for ACP sessions.",
)
@click.option("--max-steps", default=30, type=int, show_default=True)
@click.option(
    "--acp-mode",
    "acp_modes",
    multiple=True,
    default=None,
    help=(
        "Define an ACP mode preset (repeatable). Format: "
        "id:name[:provider[:model[:base_url]]]. "
        "When set, these replace the single Default mode."
    ),
)
@click.option(
    "--acp-modes-json",
    default=None,
    help="JSON string defining ACP modes array (alternative to --acp-mode).",
)
def acp(
    approval_policy: str,
    max_steps: int,
    acp_modes: tuple[str, ...],
    acp_modes_json: str | None,
) -> None:
    """Run Coding Agent as an ACP stdio agent."""
    asyncio.run(
        _run_acp_stdio(
            click.get_current_context(),
            approval_policy,
            max_steps,
            acp_modes,
            acp_modes_json,
        )
    )


async def _run_acp_stdio(
    ctx: click.Context,
    approval_policy: str,
    max_steps: int,
    acp_modes: tuple[str, ...],
    acp_modes_json: str | None,
) -> None:
    configure_tracing(enabled=False)
    data_dir = local_data_dir()
    manager = create_local_cli_session_manager(
        storage_config=local_sqlite_storage_config(data_dir),
        owner_store=SQLiteSessionOwnerStore(local_sqlite_path(data_dir)),
        owner_id=f"acp-stdio:{uuid.uuid4().hex}",
        fencing_token=time.time_ns(),
    )

    modes: list[AcpMode] | None = None
    if acp_modes:
        modes = [_parse_acp_mode(m) for m in acp_modes]
    elif acp_modes_json:
        try:
            raw = json.loads(acp_modes_json)
            modes = [AcpMode(**m) for m in raw]
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            raise click.BadParameter(
                f"Invalid --acp-modes-json: {exc}"
            ) from exc

    try:
        await manager.start_owner_lease_renewal()
        server = AcpServer(
            manager,
            approval_policy=ApprovalPolicy(approval_policy),
            provider_name=_shared_cli_arg(ctx, "provider"),
            model_name=_shared_cli_arg(ctx, "model"),
            base_url=_shared_cli_arg(ctx, "base_url"),
            max_steps=max_steps,
            modes=modes,
        )
        await run_stdio(server)
    finally:
        await manager.close()


__all__ = ["acp"]
