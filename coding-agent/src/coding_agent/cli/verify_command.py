from __future__ import annotations

from pathlib import Path

import click

from coding_agent.verification import VerificationRunner, load_task_packet_contract


@click.command()
@click.option(
    "--task-packet",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to the task packet markdown file.",
)
@click.option(
    "--mode",
    default="run",
    type=click.Choice(["run", "checklist"]),
    help="Whether to execute verification or print a human checklist.",
)
@click.option(
    "--repo",
    default=Path("."),
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Repository root used as the working directory for verification commands.",
)
def verify(task_packet: Path, mode: str, repo: Path) -> None:
    """Verify a task packet or print its checklist."""
    try:
        contract = load_task_packet_contract(task_packet)
    except ValueError as exc:
        raise click.ClickException(f"Invalid task packet: {exc}") from exc
    runner = VerificationRunner()

    if mode == "checklist":
        click.echo(runner.render_checklist(contract).text)
        return

    report = runner.run(contract, repo_root=repo)
    for step in report.steps:
        status = "PASS" if step.passed else "FAIL"
        click.echo(f"[{status}] {step.name}")
        click.echo(f"  $ {step.command}")
        if step.stdout:
            click.echo(f"  stdout: {step.stdout.rstrip()}")
        if step.stderr:
            click.echo(f"  stderr: {step.stderr.rstrip()}")
        if not step.passed:
            click.echo(f"  exit_code: {step.exit_code}")
    click.echo(report.verdict)
    if report.verdict != "VERIFIED":
        raise SystemExit(1)
