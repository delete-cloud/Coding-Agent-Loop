from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from agentkit.checkpoint import CheckpointService
from agentkit.storage.protocols import TapeStore
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape

from coding_agent.approval import ApprovalPolicy

CHECKPOINT_SESSION_CONFIG_KEY = "session_restart_config"


@dataclass(frozen=True)
class CheckpointSessionConfig:
    provider_name: str | None
    model_name: str | None
    base_url: str | None
    max_steps: int
    approval_policy: ApprovalPolicy


@dataclass(frozen=True)
class CheckpointRestoredRuntime:
    pipeline: Any
    ctx: Any
    adapter: Any


class CheckpointRestoreSession(Protocol):
    id: str
    tape_id: str | None
    provider_name: str | None
    model_name: str | None
    base_url: str | None
    max_steps: int
    approval_policy: ApprovalPolicy
    provider: Any | None
    runtime_pipeline: Any | None
    runtime_ctx: Any | None
    runtime_adapter: Any | None


PrepareCheckpointRuntime = Callable[
    ...,
    Awaitable[CheckpointRestoredRuntime],
]
CloseCheckpointRuntime = Callable[[CheckpointRestoreSession], Awaitable[None]]
PersistCheckpointSession = Callable[[CheckpointRestoreSession], Awaitable[None]]


def serialize_checkpoint_session_config(
    session: CheckpointRestoreSession,
) -> dict[str, Any]:
    return {
        "provider_name": session.provider_name,
        "model_name": session.model_name,
        "base_url": session.base_url,
        "max_steps": session.max_steps,
        "approval_policy": session.approval_policy.value,
    }


def checkpoint_session_config_from_extra(
    session: CheckpointRestoreSession,
    extra: dict[str, Any],
) -> CheckpointSessionConfig:
    raw = extra.get(CHECKPOINT_SESSION_CONFIG_KEY)
    if raw is None:
        return CheckpointSessionConfig(
            provider_name=session.provider_name,
            model_name=session.model_name,
            base_url=session.base_url,
            max_steps=session.max_steps,
            approval_policy=session.approval_policy,
        )
    if not isinstance(raw, dict):
        raise TypeError("checkpoint session config must be an object")

    required_keys = {
        "provider_name",
        "model_name",
        "base_url",
        "max_steps",
        "approval_policy",
    }
    missing_keys = sorted(required_keys - raw.keys())
    if missing_keys:
        missing = ", ".join(missing_keys)
        raise TypeError(f"checkpoint session config is missing {missing}")

    provider_name = raw.get("provider_name")
    if provider_name is not None and not isinstance(provider_name, str):
        raise TypeError("checkpoint session config has invalid provider_name")

    model_name = raw.get("model_name")
    if model_name is not None and not isinstance(model_name, str):
        raise TypeError("checkpoint session config has invalid model_name")

    base_url = raw.get("base_url")
    if base_url is not None and not isinstance(base_url, str):
        raise TypeError("checkpoint session config has invalid base_url")

    max_steps = raw.get("max_steps")
    if not isinstance(max_steps, int):
        raise TypeError("checkpoint session config has invalid max_steps")

    approval_policy_raw = raw.get("approval_policy")
    if not isinstance(approval_policy_raw, str):
        raise TypeError("checkpoint session config has invalid approval_policy")

    return CheckpointSessionConfig(
        provider_name=provider_name,
        model_name=model_name,
        base_url=base_url,
        max_steps=max_steps,
        approval_policy=ApprovalPolicy(approval_policy_raw),
    )


@dataclass(frozen=True)
class CheckpointRestoreService:
    checkpoint_service: CheckpointService
    tape_store: TapeStore
    prepare_runtime: PrepareCheckpointRuntime
    close_runtime: CloseCheckpointRuntime
    persist_session: PersistCheckpointSession

    async def restore(
        self,
        session: CheckpointRestoreSession,
        checkpoint_id: str,
    ) -> None:
        snapshot = await self.checkpoint_service.restore(checkpoint_id)
        meta = snapshot.meta
        if session.tape_id is None:
            raise ValueError("session has no stable tape id")
        if meta.tape_id != session.tape_id:
            raise ValueError(
                f"Checkpoint {checkpoint_id} belongs to tape {meta.tape_id}, "
                f"not session tape {session.tape_id}"
            )
        if meta.entry_count != len(snapshot.tape_entries):
            raise ValueError(
                "checkpoint entry_count does not match snapshot tape_entries length"
            )
        if meta.window_start > meta.entry_count:
            raise ValueError("checkpoint window_start must be <= entry_count")

        restored_tape = Tape(
            entries=[Entry.from_dict(entry) for entry in snapshot.tape_entries],
            tape_id=session.tape_id,
            _window_start=meta.window_start,
        )
        restored_config = checkpoint_session_config_from_extra(session, snapshot.extra)
        runtime = await self.prepare_runtime(
            session=session,
            restored_tape=restored_tape,
            restored_config=restored_config,
            plugin_states=snapshot.plugin_states,
        )

        previous_provider_name = session.provider_name
        previous_model_name = session.model_name
        previous_base_url = session.base_url

        await self.close_runtime(session)
        await self.tape_store.truncate(session.tape_id, meta.entry_count)
        session.tape_id = runtime.ctx.tape.tape_id
        session.provider_name = restored_config.provider_name
        session.model_name = restored_config.model_name
        session.base_url = restored_config.base_url
        session.max_steps = restored_config.max_steps
        session.approval_policy = restored_config.approval_policy
        if (
            previous_provider_name != restored_config.provider_name
            or previous_model_name != restored_config.model_name
            or previous_base_url != restored_config.base_url
        ):
            session.provider = None
        session.runtime_pipeline = runtime.pipeline
        session.runtime_ctx = runtime.ctx
        session.runtime_adapter = runtime.adapter
        await self.persist_session(session)

        checkpoints = await self.checkpoint_service.list(runtime.ctx.tape.tape_id)
        for checkpoint_meta in checkpoints:
            if checkpoint_meta.entry_count > meta.entry_count:
                await self.checkpoint_service.delete(checkpoint_meta.checkpoint_id)


__all__ = [
    "CHECKPOINT_SESSION_CONFIG_KEY",
    "CheckpointRestoreService",
    "CheckpointRestoreSession",
    "CheckpointRestoredRuntime",
    "CheckpointSessionConfig",
    "checkpoint_session_config_from_extra",
    "serialize_checkpoint_session_config",
]
