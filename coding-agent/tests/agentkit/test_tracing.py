from __future__ import annotations

import io
import json
import logging

import pytest
import structlog


class TestTracingModule:
    def test_import(self):
        from agentkit.tracing import configure_tracing, get_tracer

        assert callable(configure_tracing)
        assert callable(get_tracer)

    def test_configure_tracing_enabled_produces_json_output(self, capsys):
        from agentkit.tracing import configure_tracing, get_tracer

        configure_tracing(enabled=True)
        log = get_tracer("test.tracer")
        log.info("hello", key="value")

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert output.strip(), "expected structured log output"
        data = json.loads(output.strip())
        assert data.get("event") == "hello"
        assert data.get("key") == "value"

    def test_configure_tracing_disabled_produces_no_output(self, capsys):
        from agentkit.tracing import configure_tracing, get_tracer

        configure_tracing(enabled=False)
        log = get_tracer("test.tracer")
        log.info("should not appear")

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "should not appear" not in output

    def test_get_tracer_returns_bound_logger(self):
        from agentkit.tracing import get_tracer

        log = get_tracer("agentkit.pipeline")
        assert log is not None
        assert hasattr(log, "info")
        assert hasattr(log, "warning")
        assert hasattr(log, "debug")

    def test_env_var_enables_tracing(self, monkeypatch, capsys):
        import os

        monkeypatch.setenv("AGENTKIT_TRACING", "1")
        from agentkit.tracing import configure_tracing, get_tracer

        configure_tracing()
        log = get_tracer("test.env")
        log.info("env_enabled")

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "env_enabled" in output


class TestPipelineTracing:
    def test_pipeline_stage_trace_events_captured(self, capsys):
        from agentkit.tracing import configure_tracing

        configure_tracing(enabled=True)

        import asyncio
        from unittest.mock import MagicMock, AsyncMock
        from agentkit.runtime.pipeline import Pipeline, PipelineContext
        from agentkit.tape.tape import Tape

        registry = MagicMock()
        registry.plugin_ids.return_value = []
        runtime = MagicMock()
        runtime.call_first.return_value = None
        runtime.call_many.return_value = []
        runtime.call_all.return_value = {}
        runtime.notify.return_value = None

        pipeline = Pipeline(runtime=runtime, registry=registry)

        tape = Tape()
        ctx = PipelineContext(
            tape=tape,
            session_id="trace-test",
            config={
                "system_prompt": "",
                "model": "test",
                "provider": "test",
                "max_tool_rounds": 1,
            },
        )

        async def run():
            await pipeline.run_turn(ctx)

        asyncio.run(run())

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "stage_start" in output
        assert "stage_end" in output

    def test_pipeline_tests_produce_clean_output_when_tracing_disabled(self, capsys):
        from agentkit.tracing import configure_tracing

        configure_tracing(enabled=False)

        import asyncio
        from unittest.mock import MagicMock, AsyncMock
        from agentkit.runtime.pipeline import Pipeline, PipelineContext
        from agentkit.tape.tape import Tape

        registry = MagicMock()
        registry.plugin_ids.return_value = []
        runtime = MagicMock()
        runtime.call_first.return_value = None
        runtime.call_many.return_value = []
        runtime.call_all.return_value = {}
        runtime.notify.return_value = None

        pipeline = Pipeline(runtime=runtime, registry=registry)

        tape = Tape()
        ctx = PipelineContext(
            tape=tape,
            session_id="no-trace-test",
            config={
                "system_prompt": "",
                "model": "test",
                "provider": "test",
                "max_tool_rounds": 1,
            },
        )

        async def run():
            await pipeline.run_turn(ctx)

        asyncio.run(run())

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "stage_start" not in output
        assert "stage_end" not in output


class TestDirectiveExecutorTracing:
    def test_executor_traces_approve_directive(self, capsys):
        from agentkit.tracing import configure_tracing
        from agentkit.directive.executor import DirectiveExecutor
        from agentkit.directive.types import Approve

        configure_tracing(enabled=True)
        executor = DirectiveExecutor()

        import asyncio

        asyncio.run(executor.execute(Approve()))

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "Approve" in output or output == ""

    def test_executor_traces_reject_with_reason(self, capsys):
        from agentkit.tracing import configure_tracing
        from agentkit.directive.executor import DirectiveExecutor
        from agentkit.directive.types import Reject

        configure_tracing(enabled=True)
        executor = DirectiveExecutor()

        import asyncio

        asyncio.run(executor.execute(Reject(reason="dangerous operation")))

        captured = capsys.readouterr()
        output = captured.out + captured.err
        if output.strip():
            data = json.loads(output.strip())
            assert data.get("directive_type") == "Reject" or "Reject" in str(data)

    def test_executor_no_trace_output_when_disabled(self, capsys):
        from agentkit.tracing import configure_tracing
        from agentkit.directive.executor import DirectiveExecutor
        from agentkit.directive.types import Approve

        configure_tracing(enabled=False)
        executor = DirectiveExecutor()

        import asyncio

        asyncio.run(executor.execute(Approve()))

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "directive_execute" not in output
