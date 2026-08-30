from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys

import tomllib


def test_agentkit_public_imports_do_not_load_coding_agent_modules() -> None:
    code = """
import sys
import agentkit

assert agentkit.AgentEngine.__module__.startswith("agentkit.")
assert agentkit.SegmentCoordinator.__module__.startswith("agentkit.")
assert agentkit.PluginRegistry.__module__.startswith("agentkit.")
assert agentkit.ToolRegistry.__module__.startswith("agentkit.")
assert agentkit.Tape.__module__.startswith("agentkit.")
assert all(
    module == "agentkit" or not module.startswith("coding_agent")
    for module in sys.modules
)
"""

    completed = _run_isolated_python(code)

    assert completed.returncode == 0, completed.stderr


def test_agentkit_source_does_not_import_coding_agent() -> None:
    source_root = Path("src/agentkit")
    offenders: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name.split(".", 1)[0] == "coding_agent" for alias in node.names
            ):
                offenders.append(path.as_posix())
            if (
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and node.module.split(".", 1)[0] == "coding_agent"
            ):
                offenders.append(path.as_posix())

    assert offenders == []


def test_coding_agent_top_level_import_keeps_heavy_dependencies_lazy() -> None:
    code = """
import builtins

real_import = builtins.__import__
blocked_prefixes = ("lancedb", "tiktoken")

def guarded_import(name, globals_=None, locals_=None, fromlist=(), level=0):
    if name.startswith(blocked_prefixes):
        raise AssertionError(f"top-level coding_agent import loaded {name}")
    return real_import(name, globals_, locals_, fromlist, level)

builtins.__import__ = guarded_import

import coding_agent

assert callable(coding_agent.get_kb)
assert callable(coding_agent.get_token_counter)
"""

    completed = _run_isolated_python(code)

    assert completed.returncode == 0, completed.stderr


def test_wheel_build_includes_agentkit_and_coding_agent_packages() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    packages = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]

    assert set(packages) == {"src/coding_agent", "src/agentkit"}


def _run_isolated_python(code: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path("src").resolve())
    return subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        cwd=Path.cwd(),
        env=env,
    )
