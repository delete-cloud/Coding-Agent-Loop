from __future__ import annotations

from pathlib import Path

from coding_agent.__main__ import create_agent
from coding_agent.environment import LocalEnvironment, SandboxedEnvironment
from coding_agent.runs import IsolationPolicy


def _isolation() -> IsolationPolicy:
    return IsolationPolicy(kind="default_local_sandbox")


def test_sandboxed_environment_wraps_local_environment_tool_config(
    tmp_path: Path,
) -> None:
    environment = SandboxedEnvironment(LocalEnvironment(tmp_path), _isolation())

    assert isinstance(environment, LocalEnvironment)
    assert environment.workspace_root == tmp_path.resolve()
    assert environment.inner.workspace_summary().local_root == str(tmp_path.resolve())
    assert environment.kind == "sandboxed:local"
    assert environment.tool_config() == {
        "workspace_root": str(tmp_path.resolve()),
        "shell": {"sandbox_mode": "native"},
        "isolation_policy": _isolation().to_dict(),
    }


def test_create_agent_merges_sandboxed_environment_tool_config(
    tmp_path: Path,
) -> None:
    environment = SandboxedEnvironment(LocalEnvironment(tmp_path), _isolation())

    _pipeline, ctx = create_agent(
        environment=environment,
        session_id_override="session-1",
        run_id_override="run-1",
    )

    assert ctx.config["environment"] is environment
    assert ctx.config["workspace_root"] == str(tmp_path.resolve())
    assert ctx.config["shell"]["sandbox_mode"] == "native"
    assert ctx.config["isolation_policy"] == _isolation().to_dict()
