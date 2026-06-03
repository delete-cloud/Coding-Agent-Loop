from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentkit.environment import Environment

from coding_agent.environment.cloud import CloudEnvironment, CloudWorkspaceClient
from coding_agent.environment.local import LocalEnvironment
from coding_agent.environment.sandboxed import sandbox_environment
from coding_agent.runs.target import (
    CloudWorkspaceRef,
    LocalPathWorkspaceRef,
    RunTarget,
)


@dataclass(frozen=True)
class RuntimeEnvironmentResolverService:
    cloud_client_factory: Callable[[CloudWorkspaceRef], CloudWorkspaceClient] | None = None

    def resolve_environment_for_run_target(
        self,
        target: RunTarget | None,
    ) -> Environment:
        if target is None:
            raise RuntimeError("session is missing default_run_target")

        workspace = target.workspace
        if isinstance(workspace, LocalPathWorkspaceRef):
            environment = LocalEnvironment(Path(workspace.path).expanduser().resolve())
            return sandbox_environment(environment, target.isolation)
        if isinstance(workspace, CloudWorkspaceRef):
            if self.cloud_client_factory is None:
                raise RuntimeError("cloud workspace environment is not configured")
            environment = CloudEnvironment(self.cloud_client_factory(workspace))
            return sandbox_environment(environment, target.isolation)
        raise ValueError(
            f"runtime builders cannot resolve workspace target: {workspace.kind}"
        )

    def workspace_root_for_environment(self, environment: Environment) -> Path | None:
        local_root = environment.workspace_summary().local_root
        if local_root is None:
            return None
        return Path(local_root).expanduser().resolve()


__all__ = ["RuntimeEnvironmentResolverService"]
