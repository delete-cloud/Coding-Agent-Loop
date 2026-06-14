from __future__ import annotations

from pathlib import Path

import click

from coding_agent.core.postmortem_phase1 import build_phase1_artifacts


@click.group()
def postmortem():
    """Postmortem knowledge-base tooling."""


@postmortem.command("phase1")
@click.option(
    "--repo",
    default=Path("."),
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Repository root used for git-history collection.",
)
@click.option(
    "--output-dir",
    default="postmortem",
    help="Output directory relative to --repo unless absolute.",
)
def postmortem_phase1(repo: Path, output_dir: str) -> None:
    """Generate the Phase 1 postmortem onboarding artifacts."""
    target_output = Path(output_dir)
    if not target_output.is_absolute():
        target_output = repo / target_output
    result = build_phase1_artifacts(repo, output_dir=target_output)
    click.echo(
        "Generated Phase 1 postmortem onboarding artifacts "
        f"at {result.output_dir} ({result.pattern_count} patterns from {result.commit_count} fix commits)."
    )
