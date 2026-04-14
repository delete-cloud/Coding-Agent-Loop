from __future__ import annotations

from collections.abc import Awaitable
from importlib import import_module
from typing import Protocol, cast

from agentkit._types import JsonDict
from coding_agent.evaluation.adapter import EvaluationTestCase, EvaluationToolCall


class _ToolCallCtor(Protocol):
    def __call__(
        self,
        *,
        name: str,
        input_parameters: JsonDict | None = None,
        output: object | None = None,
        description: str | None = None,
        reasoning: str | None = None,
    ) -> object: ...


class _LLMTestCaseCtor(Protocol):
    def __call__(
        self,
        *,
        input: str,
        actual_output: str,
        tools_called: list[object],
        expected_tools: list[object],
    ) -> object: ...


class _ToolCorrectnessMetricCtor(Protocol):
    def __call__(self, *, threshold: float = 1.0) -> object: ...


class _AsyncMetric(Protocol):
    def a_measure(self, test_case: object) -> Awaitable[object]: ...


class _SyncMetric(Protocol):
    def measure(self, test_case: object) -> object: ...


class _MetricWithScore(Protocol):
    score: object


def _get_module_attr(module_name: str, attr_name: str) -> object:
    module = import_module(module_name)
    return cast(object, getattr(module, attr_name))


def _import_deepeval_test_case() -> tuple[_LLMTestCaseCtor, _ToolCallCtor]:
    llm_test_case = _get_module_attr("deepeval.test_case", "LLMTestCase")
    tool_call = _get_module_attr("deepeval.test_case", "ToolCall")
    return cast(_LLMTestCaseCtor, llm_test_case), cast(_ToolCallCtor, tool_call)


def _import_tool_correctness_metric() -> _ToolCorrectnessMetricCtor:
    metric = _get_module_attr("deepeval.metrics", "ToolCorrectnessMetric")
    return cast(_ToolCorrectnessMetricCtor, metric)


def _to_deepeval_tool_call(tool: EvaluationToolCall) -> object:
    _, tool_call_ctor = _import_deepeval_test_case()
    return tool_call_ctor(
        name=tool.name,
        input_parameters=tool.input_parameters,
        output=tool.output,
        description=tool.description,
        reasoning=tool.reasoning,
    )


def to_deepeval_test_case(case: EvaluationTestCase) -> object:
    llm_test_case_ctor, _ = _import_deepeval_test_case()
    return llm_test_case_ctor(
        input=case.input,
        actual_output=case.actual_output,
        tools_called=[_to_deepeval_tool_call(tool) for tool in case.tools_called],
        expected_tools=[_to_deepeval_tool_call(tool) for tool in case.expected_tools],
    )


def make_tool_correctness_metric(*, threshold: float = 1.0) -> object:
    metric_ctor = _import_tool_correctness_metric()
    return metric_ctor(threshold=threshold)


async def metric_measure(metric: object, case: EvaluationTestCase) -> float:
    deepeval_case = to_deepeval_test_case(case)

    if hasattr(metric, "a_measure"):
        async_metric = cast(_AsyncMetric, metric)
        result = await async_metric.a_measure(deepeval_case)
    else:
        sync_metric = cast(_SyncMetric, metric)
        result = sync_metric.measure(deepeval_case)

    if isinstance(result, int | float):
        return float(result)

    score = cast(_MetricWithScore, metric).score if hasattr(metric, "score") else None
    if isinstance(score, int | float):
        return float(score)

    raise TypeError("metric did not return or expose a numeric score")
