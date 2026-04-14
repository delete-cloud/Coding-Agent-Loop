from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from coding_agent.evaluation import EvaluationTestCase, EvaluationToolCall


def _sample_case() -> EvaluationTestCase:
    return EvaluationTestCase(
        input="parent task",
        actual_output="parent done",
        tools_called=(
            EvaluationToolCall(
                name="subagent",
                input_parameters={"goal": "child task"},
                output="Subagent completed: child done",
            ),
        ),
        expected_tools=(
            EvaluationToolCall(
                name="subagent",
                input_parameters={"goal": "child task"},
            ),
        ),
        metadata={"threshold": 1.0},
    )


class TestMetricHelpers:
    def test_make_tool_correctness_metric_raises_when_deepeval_missing(self) -> None:
        from coding_agent.evaluation.metrics import make_tool_correctness_metric

        original = sys.modules.pop("deepeval", None)
        original_metrics = sys.modules.pop("deepeval.metrics", None)
        original_test_case = sys.modules.pop("deepeval.test_case", None)
        try:
            with pytest.raises(ModuleNotFoundError):
                make_tool_correctness_metric()
        finally:
            if original is not None:
                sys.modules["deepeval"] = original
            if original_metrics is not None:
                sys.modules["deepeval.metrics"] = original_metrics
            if original_test_case is not None:
                sys.modules["deepeval.test_case"] = original_test_case

    def test_to_deepeval_test_case_maps_tools_and_metadata(self, monkeypatch) -> None:
        class FakeToolCall:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        class FakeLLMTestCase:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        monkeypatch.setitem(sys.modules, "deepeval", ModuleType("deepeval"))
        monkeypatch.setitem(
            sys.modules,
            "deepeval.test_case",
            SimpleNamespace(LLMTestCase=FakeLLMTestCase, ToolCall=FakeToolCall),
        )

        from coding_agent.evaluation.metrics import to_deepeval_test_case

        result = to_deepeval_test_case(_sample_case())

        assert result.input == "parent task"
        assert result.actual_output == "parent done"
        assert [tool.name for tool in result.tools_called] == ["subagent"]
        assert [tool.name for tool in result.expected_tools] == ["subagent"]
        assert result.tools_called[0].input_parameters == {"goal": "child task"}
        assert result.tools_called[0].output == "Subagent completed: child done"

    def test_make_tool_correctness_metric_uses_kimi_code_judge_when_available(
        self, monkeypatch
    ) -> None:
        class FakeMetric:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class FakeGPTModel:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        monkeypatch.setenv("KIMI_CODE_API_KEY", "sk-kimi-code")
        monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
        monkeypatch.setitem(sys.modules, "deepeval", ModuleType("deepeval"))
        monkeypatch.setitem(
            sys.modules,
            "deepeval.metrics",
            SimpleNamespace(ToolCorrectnessMetric=FakeMetric),
        )
        monkeypatch.setitem(
            sys.modules,
            "deepeval.models",
            SimpleNamespace(GPTModel=FakeGPTModel),
        )

        from coding_agent.evaluation.metrics import make_tool_correctness_metric

        metric = make_tool_correctness_metric(threshold=0.7)

        assert metric.kwargs["threshold"] == 0.7
        assert metric.kwargs["model"].kwargs == {
            "model": "kimi-for-coding",
            "api_key": "sk-kimi-code",
            "base_url": "https://api.kimi.com/coding/v1",
        }

    @pytest.mark.asyncio
    async def test_metric_measure_supports_async_metrics(self, monkeypatch) -> None:
        class FakeToolCall:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        class FakeLLMTestCase:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        monkeypatch.setitem(sys.modules, "deepeval", ModuleType("deepeval"))
        monkeypatch.setitem(
            sys.modules,
            "deepeval.test_case",
            SimpleNamespace(LLMTestCase=FakeLLMTestCase, ToolCall=FakeToolCall),
        )

        from coding_agent.evaluation.metrics import metric_measure

        class AsyncMetric:
            async def a_measure(self, test_case):
                assert test_case.input == "parent task"
                return 1.0

        score = await metric_measure(AsyncMetric(), _sample_case())

        assert score == 1.0
