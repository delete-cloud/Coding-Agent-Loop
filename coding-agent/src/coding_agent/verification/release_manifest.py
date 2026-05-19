from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import yaml
from pydantic import BaseModel, ConfigDict

from coding_agent.verification.contract import VerificationContract, VerificationStep


_FORBIDDEN_SHELL_TOKENS = ("&&", "||", "|", ";", ">", "<", "&")


class ReleaseVerificationGate(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: str
    command: str
    required: bool = True
    scope: str


class ReleaseVerificationManifest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str
    description: str
    source_path: Path
    gates: list[ReleaseVerificationGate]

    def to_verification_contract(self) -> VerificationContract:
        return VerificationContract(
            source_path=self.source_path,
            steps=[
                VerificationStep(name=gate.id, command=gate.command)
                for gate in self.gates
            ],
        )


def load_release_verification_manifest(path: Path) -> ReleaseVerificationManifest:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Release verification manifest must be a mapping")

    name = _required_string(payload, "name")
    description = _required_string(payload, "description")
    gates_payload = payload.get("gates")
    if not isinstance(gates_payload, list) or not gates_payload:
        raise ValueError("Release verification manifest requires at least one gate")

    gates: list[ReleaseVerificationGate] = []
    seen_ids: set[str] = set()
    for index, raw_gate in enumerate(gates_payload, start=1):
        if not isinstance(raw_gate, dict):
            raise ValueError(f"Release verification gate {index} must be a mapping")
        gate_id = _required_string(raw_gate, "id")
        if gate_id in seen_ids:
            raise ValueError(
                f"Release verification manifest has duplicate gate id: {gate_id}"
            )
        seen_ids.add(gate_id)

        command = _required_string(raw_gate, "command")
        _reject_shell_syntax(command)
        required = raw_gate.get("required", True)
        if not isinstance(required, bool):
            raise ValueError(
                f"Release verification gate {gate_id} required must be boolean"
            )
        scope = _required_string(raw_gate, "scope")
        gates.append(
            ReleaseVerificationGate(
                id=gate_id,
                command=command,
                required=required,
                scope=scope,
            )
        )

    return ReleaseVerificationManifest(
        name=name,
        description=description,
        source_path=path,
        gates=gates,
    )


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Release verification manifest field {key} must be a string")
    return value


def _reject_shell_syntax(command: str) -> None:
    if any(token in command for token in _FORBIDDEN_SHELL_TOKENS):
        raise ValueError("Release verification commands must not use shell syntax")
