from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import click


@click.command()
@click.option("--session", "-s", help="Session ID (default: last)")
def stats(session: str | None):
    """Show session statistics."""
    from coding_agent.observability.metrics import collector

    if not session:
        # Use last session
        sessions = collector.list_sessions()
        if not sessions:
            click.echo("No sessions found.")
            return
        session = sessions[-1]

    metrics = collector.get_session(session)
    if not metrics:
        click.echo(f"Session {session} not found.")
        return

    data = metrics.to_dict()
    tool_calls = cast(Mapping[str, int], data["tool_calls"])

    click.echo(f"Session: {data['session_id']}")
    click.echo(f"Duration: {data['duration']}")
    click.echo(f"\nTools: {data['tools_total']} calls")
    for tool, count in tool_calls.items():
        click.echo(f"  • {tool}: {count}")
    click.echo(
        f"\nAPI: {data['api_calls']} calls, avg latency {data['avg_api_latency']}"
    )
    click.echo(f"Cache hit rate: {data['cache_hit_rate']}")
    click.echo(f"Tokens: {data['tokens_input']} in / {data['tokens_output']} out")
