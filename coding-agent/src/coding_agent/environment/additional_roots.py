from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentkit.environment import Environment, FileTools, WorkspaceSummary


class AdditionalWorkspaceRootsEnvironment:
    """Environment wrapper that activates ACP additional workspace roots."""

    def __init__(
        self,
        environment: Environment,
        additional_roots: list[Path | str] | tuple[Path | str, ...],
    ) -> None:
        workspace = environment.workspace_summary()
        if workspace.local_root is None:
            raise ValueError("additional workspace roots require a local workspace")
        self._environment = environment
        self._workspace_root = Path(workspace.local_root).expanduser().resolve()
        self._additional_roots = tuple(
            Path(root).expanduser().resolve() for root in additional_roots
        )

    @property
    def kind(self) -> str:
        return self._environment.kind

    @property
    def inner(self) -> Environment:
        return self._environment

    def tool_config(self) -> dict[str, Any]:
        config = dict(self._environment.tool_config())
        config["additional_workspace_roots"] = [
            str(root) for root in self._additional_roots
        ]
        return config

    def workspace_summary(self) -> WorkspaceSummary:
        return self._environment.workspace_summary()

    def build_file_tools(self) -> FileTools:
        from coding_agent.tools.file_ops import build_file_tools

        return build_file_tools(
            self._workspace_root,
            additional_roots=self._additional_roots,
        )

    def build_file_patch_tool(self) -> Callable[[str, str], str]:
        from coding_agent.tools.file_patch_tool import build_file_patch_tool

        return build_file_patch_tool(
            self._workspace_root,
            additional_roots=self._additional_roots,
        )

    def build_shell_tool(self) -> Callable[..., Any]:
        return self._environment.build_shell_tool()


def with_additional_workspace_roots(
    environment: Environment,
    additional_roots: list[Path | str] | tuple[Path | str, ...],
) -> Environment:
    if not additional_roots:
        return environment
    return AdditionalWorkspaceRootsEnvironment(environment, additional_roots)


__all__ = [
    "AdditionalWorkspaceRootsEnvironment",
    "with_additional_workspace_roots",
]
