"""Bee command intent bridge.

This module resolves Bee node command references to workspace-declared intents.
It does not execute commands or grant policy permissions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from coding_agent.bee_runtime import BeeNodeManifest
from coding_agent.bee_workspace import (
    BeeWorkspaceCommandIntent,
    BeeWorkspaceTemplate,
    load_bee_workspace_command_intents,
)

BeeCommandIntentResolutionStatus = Literal[
    "resolved",
    "missing_command_ref",
    "unknown_command_ref",
    "disabled_intent",
]


@dataclass(frozen=True)
class BeeCommandIntentResolution:
    status: BeeCommandIntentResolutionStatus
    template_id: str
    node_id: str
    command_ref: str | None
    intent: BeeWorkspaceCommandIntent | None = None
    reason: str | None = None
    will_execute: bool = False


def resolve_bee_command_intent(
    *,
    template: BeeWorkspaceTemplate,
    node: BeeNodeManifest,
) -> BeeCommandIntentResolution:
    """Resolve a Bee node command_ref to a non-executing workspace intent."""

    command_ref = node.command_ref
    if command_ref is None:
        return BeeCommandIntentResolution(
            status="missing_command_ref",
            template_id=template.template_id,
            node_id=node.node_id,
            command_ref=None,
            reason="node has no command_ref",
        )

    intents_by_name = {
        intent.name: intent for intent in load_bee_workspace_command_intents(template)
    }
    intent = intents_by_name.get(command_ref)
    if intent is None:
        return BeeCommandIntentResolution(
            status="unknown_command_ref",
            template_id=template.template_id,
            node_id=node.node_id,
            command_ref=command_ref,
            reason="command_ref is not declared by template commands.yaml",
        )
    if intent.status == "disabled":
        return BeeCommandIntentResolution(
            status="disabled_intent",
            template_id=template.template_id,
            node_id=node.node_id,
            command_ref=command_ref,
            intent=intent,
            reason="declared command intent is disabled",
        )
    return BeeCommandIntentResolution(
        status="resolved",
        template_id=template.template_id,
        node_id=node.node_id,
        command_ref=command_ref,
        intent=intent,
        reason="command_ref resolved to workspace intent metadata",
    )
