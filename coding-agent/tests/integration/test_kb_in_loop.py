from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

import coding_agent.core.app as app_module
from agentkit.providers.models import DoneEvent, TextEvent
from coding_agent.adapter import PipelineAdapter
from coding_agent.adapter.types import StopReason
from coding_agent.kb import KB
from coding_agent.plugins.kb import KBPlugin


SENTINEL_FACT = "KB_SENTINEL_FACT_loop_rag_injected_73f4c2"


def _fake_embed(texts: list[str]) -> list[list[float]]:
    return [[0.0] * 8 for _ in texts]


def _write_kb_enabled_config(config_path: Path) -> None:
    config_path.write_text(
        """
[agent]
name = "coding-agent"
model = "mock-model"
provider = "anthropic"
system_prompt = "You are a test agent."
max_turns = 1

[agent.plugins]
enabled = [
    "llm_provider",
    "core_tools",
    "kb",
]

[kb]
db_path = "kb"
embedding_model = "fake-embedding-model"
embedding_dim = 8
chunk_size = 1200
chunk_overlap = 0
top_k = 1
index_extensions = [".md"]
""".strip()
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_kb_plugin_injects_indexed_context_into_agent_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    config_path = tmp_path / "agent.toml"
    _write_kb_enabled_config(config_path)

    kb = KB(db_path=data_dir / "kb", embedding_dim=8, embedding_fn=_fake_embed)
    await kb.index_file(
        Path("docs/sentinel.md"),
        f"The in-loop RAG fixture fact is {SENTINEL_FACT}.",
    )

    def fail_embedding_endpoint(self: KB) -> object:
        raise AssertionError("test must not use a real embedding endpoint")

    monkeypatch.setattr(KB, "_get_openai_client", fail_embedding_endpoint)
    monkeypatch.setattr(KB, "_get_openai_sync_client", fail_embedding_endpoint)

    class TestKBPlugin(KBPlugin):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs, embedding_fn=_fake_embed)

    monkeypatch.setattr(app_module, "KBPlugin", TestKBPlugin)

    pipeline, ctx = app_module.create_agent(
        config_path=config_path,
        data_dir=data_dir,
        api_key="sk-test",
        approval_mode_override="yolo",
    )
    assert "kb" in pipeline._registry.plugin_ids()

    captured_messages: list[list[dict[str, Any]]] = []

    async def mock_stream(messages, tools=None, **kwargs):
        del tools, kwargs
        captured_messages.append(messages)
        yield TextEvent(text="ack")
        yield DoneEvent()

    mock_provider = AsyncMock()
    mock_provider.stream = mock_stream
    pipeline._registry.get("llm_provider")._instance = mock_provider

    await pipeline.mount(ctx)
    assert ctx.plugin_states["kb"]["has_table"] is True

    adapter = PipelineAdapter(pipeline, ctx, consumer=None)
    outcome = await adapter.run_turn("What does the in-loop RAG fixture say?")

    assert outcome.stop_reason == StopReason.NO_TOOL_CALLS
    assert outcome.final_message == "ack"
    assert len(captured_messages) == 1
    serialized_context = repr(captured_messages[0])
    assert "[Context Pack]" in serialized_context
    assert "## Repo references" in serialized_context
    assert SENTINEL_FACT in serialized_context
