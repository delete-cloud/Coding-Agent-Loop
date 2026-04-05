from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from agentkit.context.builder import ContextBuilder
from agentkit.plugin.registry import PluginRegistry
from agentkit.providers.models import DoneEvent, TextEvent, ToolCallEvent
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.runtime.pipeline import Pipeline, PipelineContext
from agentkit.tape.anchor import Anchor
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from agentkit.tape.view import TapeView


ORIGINAL_FROM_TAPE = TapeView.from_tape.__func__
ORIGINAL_BUILD_CORE_MESSAGES = ContextBuilder.build_core_messages
ORIGINAL_CONTEXT_BUILDER_INIT = ContextBuilder.__init__


class MultiRoundProvider:
    def __init__(self, tool_rounds: int) -> None:
        self._tool_rounds = tool_rounds
        self._call_count = 0
        self.messages_seen: list[list[dict[str, Any]]] = []

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ):
        self.messages_seen.append(deepcopy(messages))
        self._call_count += 1

        if self._call_count <= self._tool_rounds:
            yield ToolCallEvent(
                tool_call_id=f"tc-{self._call_count}",
                name="demo_tool",
                arguments={"round": self._call_count},
            )
            yield DoneEvent()
            return

        yield TextEvent(text="done")
        yield DoneEvent()


class SimpleReplyProvider:
    def __init__(self) -> None:
        self.messages_seen: list[list[dict[str, Any]]] = []

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ):
        self.messages_seen.append(deepcopy(messages))
        yield TextEvent(text="done")
        yield DoneEvent()


class IncrementalContextPlugin:
    state_key = "incremental_test"

    def __init__(
        self,
        provider: Any,
        append_checkpoint_anchor: bool = False,
        enable_window_resolution: bool = False,
    ) -> None:
        self._provider = provider
        self._append_checkpoint_anchor = append_checkpoint_anchor
        self._enable_window_resolution = enable_window_resolution
        self._checkpoint_count = 0
        self._resolve_call_count = 0

    def hooks(self) -> dict[str, Any]:
        return {
            "mount": self.do_mount,
            "provide_llm": self.provide_llm,
            "provide_storage": self.provide_storage,
            "get_tools": self.get_tools,
            "build_context": self.build_context,
            "execute_tool": self.execute_tool,
            "on_checkpoint": self.on_checkpoint,
            "resolve_context_window": self.resolve_context_window,
        }

    def do_mount(self) -> dict[str, object]:
        return {}

    def provide_llm(self, **kwargs: Any) -> Any:
        return self._provider

    def provide_storage(self, **kwargs: Any) -> None:
        return None

    def get_tools(self, **kwargs: Any) -> list[object]:
        return []

    def build_context(self, tape: Tape, **kwargs: Any) -> list[dict[str, str]]:
        tool_results = sum(
            1 for entry in tape.snapshot() if entry.kind == "tool_result"
        )
        return [{"role": "system", "content": f"grounding:{tool_results}"}]

    def execute_tool(
        self,
        name: str = "",
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        if name != "demo_tool":
            raise AssertionError(f"unexpected tool: {name}")
        if arguments is None:
            raise AssertionError("arguments missing")
        return f"result:{arguments['round']}"

    def resolve_context_window(
        self, tape: Tape, **kwargs: Any
    ) -> tuple[int, Anchor] | None:
        if not self._enable_window_resolution:
            return None
        if len(tape) < 3:
            return None
        self._resolve_call_count += 1
        if self._resolve_call_count not in {1, 2}:
            return None
        return (
            max(len(tape.windowed_entries()) - 1, 0),
            Anchor(
                anchor_type="handoff",
                payload={"content": f"summary-{self._resolve_call_count}"},
                meta={"prefix": "Context Summary"},
            ),
        )

    def on_checkpoint(self, ctx: Any = None, **kwargs: Any) -> None:
        if ctx is None or not self._append_checkpoint_anchor:
            return
        if self._checkpoint_count > 0:
            return

        self._checkpoint_count += 1
        ctx.tape.append(
            Anchor(
                anchor_type="topic_start",
                payload={"content": "checkpoint anchor"},
                meta={"prefix": "Topic Start"},
            )
        )


async def _run_multi_round_turn(
    incremental_context: bool,
) -> tuple[list[list[dict[str, Any]]], list[dict[str, Any]]]:
    provider = MultiRoundProvider(tool_rounds=10)
    plugin = IncrementalContextPlugin(provider)
    registry = PluginRegistry()
    registry.register(plugin)
    pipeline = Pipeline(runtime=HookRuntime(registry), registry=registry)

    tape = Tape()
    tape.append(Entry(kind="message", payload={"role": "user", "content": "start"}))
    ctx = PipelineContext(
        tape=tape,
        session_id="ses-1",
        config={
            "system_prompt": "system",
            "incremental_context": incremental_context,
            "incremental_context_rebuild_interval": 5,
        },
    )
    await pipeline.mount(ctx)
    await pipeline.run_turn(ctx)
    return provider.messages_seen, ctx.messages


async def _count_full_rebuild_calls(
    monkeypatch: pytest.MonkeyPatch,
    incremental_context: bool,
) -> dict[str, int]:
    counts = {"from_tape": 0, "build_core": 0}

    def counting_from_tape(cls, tape: Tape):
        counts["from_tape"] += 1
        return ORIGINAL_FROM_TAPE(cls, tape)

    def counting_build_core_messages(
        self,
        entries: list[Entry],
    ) -> list[dict[str, Any]]:
        counts["build_core"] += 1
        return ORIGINAL_BUILD_CORE_MESSAGES(self, entries)

    monkeypatch.setattr(TapeView, "from_tape", classmethod(counting_from_tape))
    monkeypatch.setattr(
        ContextBuilder,
        "build_core_messages",
        counting_build_core_messages,
    )

    await _run_multi_round_turn(incremental_context=incremental_context)
    return counts


async def _run_two_turns_with_checkpoint_anchor(
    incremental_context: bool,
) -> list[list[dict[str, Any]]]:
    provider = SimpleReplyProvider()
    plugin = IncrementalContextPlugin(provider, append_checkpoint_anchor=True)
    registry = PluginRegistry()
    registry.register(plugin)
    pipeline = Pipeline(runtime=HookRuntime(registry), registry=registry)

    tape = Tape()
    ctx = PipelineContext(
        tape=tape,
        session_id="ses-2",
        config={
            "system_prompt": "system",
            "incremental_context": incremental_context,
            "incremental_context_rebuild_interval": 5,
        },
    )
    await pipeline.mount(ctx)

    ctx.tape.append(Entry(kind="message", payload={"role": "user", "content": "first"}))
    await pipeline.run_turn(ctx)

    ctx.tape.append(
        Entry(kind="message", payload={"role": "user", "content": "second"})
    )
    await pipeline.run_turn(ctx)

    return provider.messages_seen


async def _run_two_turns_after_tape_shrink(
    incremental_context: bool,
) -> list[list[dict[str, Any]]]:
    provider = SimpleReplyProvider()
    plugin = IncrementalContextPlugin(provider)
    registry = PluginRegistry()
    registry.register(plugin)
    pipeline = Pipeline(runtime=HookRuntime(registry), registry=registry)

    tape = Tape()
    ctx = PipelineContext(
        tape=tape,
        session_id="ses-3",
        config={
            "system_prompt": "system",
            "incremental_context": incremental_context,
            "incremental_context_rebuild_interval": 5,
        },
    )
    await pipeline.mount(ctx)

    ctx.tape.append(Entry(kind="message", payload={"role": "user", "content": "first"}))
    await pipeline.run_turn(ctx)

    shrunk_tape = Tape()
    shrunk_tape.append(
        Anchor(
            anchor_type="handoff",
            payload={"content": "summary"},
            meta={"prefix": "Context Summary"},
        )
    )
    ctx.tape = shrunk_tape
    ctx.tape.append(
        Entry(kind="message", payload={"role": "user", "content": "second"})
    )
    await pipeline.run_turn(ctx)

    return provider.messages_seen


async def _run_three_turns_with_repeated_handoff(
    incremental_context: bool,
) -> list[list[dict[str, Any]]]:
    provider = SimpleReplyProvider()
    plugin = IncrementalContextPlugin(provider, enable_window_resolution=True)
    registry = PluginRegistry()
    registry.register(plugin)
    pipeline = Pipeline(runtime=HookRuntime(registry), registry=registry)

    tape = Tape()
    ctx = PipelineContext(
        tape=tape,
        session_id="ses-4",
        config={
            "system_prompt": "system",
            "incremental_context": incremental_context,
            "incremental_context_rebuild_interval": 5,
        },
    )
    await pipeline.mount(ctx)

    for label in ("first", "second", "third"):
        ctx.tape.append(
            Entry(kind="message", payload={"role": "user", "content": label})
        )
        ctx.tape.append(
            Entry(
                kind="message", payload={"role": "assistant", "content": f"ack-{label}"}
            )
        )
        await pipeline.run_turn(ctx)

    return provider.messages_seen


@pytest.mark.asyncio
async def test_incremental_context_matches_full_rebuild_for_ten_tool_rounds() -> None:
    full_messages, full_final_messages = await _run_multi_round_turn(
        incremental_context=False
    )
    incremental_messages, incremental_final_messages = await _run_multi_round_turn(
        incremental_context=True
    )

    assert len(full_messages) == 11
    assert incremental_messages == full_messages
    assert incremental_final_messages == full_final_messages


@pytest.mark.asyncio
async def test_incremental_context_skips_full_rebuild_work_between_intervals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full_counts = await _count_full_rebuild_calls(
        monkeypatch, incremental_context=False
    )
    incremental_counts = await _count_full_rebuild_calls(
        monkeypatch, incremental_context=True
    )

    assert full_counts == {"from_tape": 11, "build_core": 11}
    assert incremental_counts == {"from_tape": 3, "build_core": 3}


@pytest.mark.asyncio
async def test_incremental_context_rebuild_falls_back_when_checkpoint_appends_anchor() -> (
    None
):
    full_messages = await _run_two_turns_with_checkpoint_anchor(
        incremental_context=False
    )
    incremental_messages = await _run_two_turns_with_checkpoint_anchor(
        incremental_context=True
    )

    assert len(full_messages) == 2
    assert incremental_messages == full_messages
    assert any(
        message.get("content") == "[Topic Start] checkpoint anchor"
        for message in incremental_messages[1]
    )


@pytest.mark.asyncio
async def test_incremental_context_rebuild_falls_back_when_tape_shrinks() -> None:
    full_messages = await _run_two_turns_after_tape_shrink(incremental_context=False)
    incremental_messages = await _run_two_turns_after_tape_shrink(
        incremental_context=True
    )

    assert incremental_messages == full_messages
    assert any(
        message.get("content") == "[Context Summary] summary"
        for message in incremental_messages[1]
    )


@pytest.mark.asyncio
async def test_pipeline_reuses_context_builder_within_pipeline_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_count = 0

    def counting_init(self, system_prompt: str = "") -> None:
        nonlocal init_count
        init_count += 1
        ORIGINAL_CONTEXT_BUILDER_INIT(self, system_prompt)

    monkeypatch.setattr(ContextBuilder, "__init__", counting_init)

    await _run_multi_round_turn(incremental_context=True)

    assert init_count == 1


@pytest.mark.asyncio
async def test_incremental_context_allows_repeated_handoff_across_turns() -> None:
    full_messages = await _run_three_turns_with_repeated_handoff(
        incremental_context=False
    )
    incremental_messages = await _run_three_turns_with_repeated_handoff(
        incremental_context=True
    )

    assert incremental_messages == full_messages
    assert any(
        message.get("content") == "[Context Summary] summary-1"
        for message in incremental_messages[1]
    )
    assert any(
        message.get("content") == "[Context Summary] summary-2"
        for message in incremental_messages[2]
    )
