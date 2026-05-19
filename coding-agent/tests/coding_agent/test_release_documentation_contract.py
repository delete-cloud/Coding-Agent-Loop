from __future__ import annotations

from pathlib import Path
import re
import shlex
import tomllib

from coding_agent.__main__ import main
from coding_agent.verification import load_release_verification_manifest


def test_readme_coding_agent_commands_match_cli_commands() -> None:
    documented_commands = _documented_module_commands(Path("README.md"))

    assert {"run", "repl", "serve"} <= documented_commands
    assert documented_commands <= set(main.commands)


def test_release_manifest_pytest_targets_exist() -> None:
    manifest = load_release_verification_manifest(
        Path("docs/release_hardening/release-verification.yaml")
    )

    targets: set[Path] = set()
    for gate in manifest.gates:
        targets.update(_pytest_targets(gate.command))

    assert targets
    assert sorted(target.as_posix() for target in targets if not target.exists()) == []


def test_readme_boundary_summary_matches_packaged_source_layout() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    packages = set(pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"])

    assert packages == {"src/agentkit", "src/coding_agent"}
    assert Path("src/agentkit").is_dir()
    assert Path("src/coding_agent").is_dir()
    assert "src/agentkit/` - framework/runtime code" in readme
    assert "src/coding_agent/` - the concrete coding assistant CLI" in readme
    assert re.search(r"agentkit/\s+# generic runtime", readme)
    assert re.search(r"coding_agent/\s+# application layer", readme)


def _documented_module_commands(path: Path) -> set[str]:
    commands: set[str] = set()
    for command in _bash_commands(path.read_text(encoding="utf-8")):
        tokens = shlex.split(command)
        prefix = ["uv", "run", "python", "-m", "coding_agent"]
        if tokens[: len(prefix)] != prefix:
            continue
        if len(tokens) == len(prefix):
            continue
        subcommand = tokens[len(prefix)]
        if not subcommand.startswith("-"):
            commands.add(subcommand)
    return commands


def _bash_commands(markdown: str) -> list[str]:
    commands: list[str] = []
    in_bash_block = False
    pending = ""
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line == "```bash":
            in_bash_block = True
            pending = ""
            continue
        if in_bash_block and line == "```":
            if pending:
                commands.append(pending.strip())
            in_bash_block = False
            pending = ""
            continue
        if not in_bash_block or not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1].strip() + " "
            continue
        command = (pending + line).strip()
        pending = ""
        commands.append(command)
    return commands


def _pytest_targets(command: str) -> set[Path]:
    tokens = shlex.split(command)
    assert tokens[:3] == ["uv", "run", "pytest"]

    targets: set[Path] = set()
    skip_next = False
    for token in tokens[3:]:
        if skip_next:
            skip_next = False
            continue
        if token in {"-k", "-m"}:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        targets.add(Path(token.split("::", 1)[0]))
    return targets
