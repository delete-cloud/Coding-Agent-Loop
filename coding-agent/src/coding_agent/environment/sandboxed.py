from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentkit.environment import Environment, FileTools, WorkspaceSummary

from coding_agent.environment.local import LocalEnvironment
from coding_agent.runs.target import IsolationPolicy


class SandboxedEnvironment(LocalEnvironment):
    """Environment wrapper that makes executor isolation policy explicit."""

    def __init__(
        self,
        environment: Environment,
        isolation: IsolationPolicy,
    ) -> None:
        workspace_summary = environment.workspace_summary()
        if workspace_summary.local_root is None:
            raise ValueError("sandboxed local environments require a local workspace")
        super().__init__(Path(workspace_summary.local_root))
        self._environment = environment
        self.isolation = isolation

    @property
    def kind(self) -> str:
        return f"sandboxed:{self._environment.kind}"

    @property
    def inner(self) -> Environment:
        return self._environment

    def tool_config(self) -> dict[str, Any]:
        config = dict(self._environment.tool_config())
        shell_config = config.get("shell", {})
        if shell_config is None:
            shell_config = {}
        if not isinstance(shell_config, dict):
            raise ValueError("environment shell tool config must be a dict")
        merged_shell_config = dict(shell_config)
        if self.isolation.filesystem == "workspace_scoped":
            merged_shell_config.setdefault("sandbox_mode", "none")
        config["shell"] = merged_shell_config
        config["isolation_policy"] = self.isolation.to_dict()
        return config

    def workspace_summary(self) -> WorkspaceSummary:
        return self._environment.workspace_summary()

    def build_file_tools(self) -> FileTools:
        return self._environment.build_file_tools()

    def build_file_patch_tool(self) -> Callable[[str, str], str]:
        return self._environment.build_file_patch_tool()

    def build_shell_tool(self) -> Callable[..., Any]:
        return self._environment.build_shell_tool()


def sandbox_environment(
    environment: Environment,
    isolation: IsolationPolicy,
) -> Environment:
    if isolation.kind == "default_local_sandbox":
        return SandboxedEnvironment(environment, isolation)
    return environment


__all__ = ["SandboxedEnvironment", "sandbox_environment"]
