from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentkit.environment import Environment

from coding_agent.environment.binding_resolver import BindingResolver
from coding_agent.environment.execution_binding import CloudWorkspaceBinding
from coding_agent.environment.local import LocalEnvironment
from coding_agent.environment.sandboxed import sandbox_environment
from coding_agent.runs.target import (
    CloudWorkspaceRef,
    LocalPathWorkspaceRef,
    RunTarget,
)


@dataclass(frozen=True)
class RuntimeEnvironmentResolverService:
    binding_resolver: BindingResolver

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
            environment = self.binding_resolver.resolve_environment(
                CloudWorkspaceBinding(
                    workspace_url=workspace.workspace_url,
                    workspace_id=workspace.workspace_id,
                    runtime_profile=workspace.runtime_profile,
                    workspace_provider=workspace.workspace_provider,
                    provider_instance_id=workspace.provider_instance_id,
                )
            )
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
