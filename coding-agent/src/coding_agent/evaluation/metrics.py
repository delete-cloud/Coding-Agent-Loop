from __future__ import annotations

from collections.abc import Awaitable
from importlib import import_module
import os
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
    def __call__(
        self,
        *,
        threshold: float = 1.0,
        model: object | None = None,
    ) -> object: ...


class _GPTModelCtor(Protocol):
    def __call__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
    ) -> object: ...


class _KimiModelCtor(Protocol):
    def __call__(
        self,
        *,
        model: str,
        api_key: str,
        temperature: float = 0,
    ) -> object: ...


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


def _import_gpt_model() -> _GPTModelCtor:
    model = _get_module_attr("deepeval.models", "GPTModel")
    return cast(_GPTModelCtor, model)


def _import_kimi_model() -> _KimiModelCtor:
    model = _get_module_attr("deepeval.models", "KimiModel")
    return cast(_KimiModelCtor, model)


def _build_metric_judge_model() -> object | None:
    kimi_code_api_key = os.environ.get("KIMI_CODE_API_KEY")
    if kimi_code_api_key:
        gpt_model_ctor = _import_gpt_model()
        return gpt_model_ctor(
            model="kimi-for-coding",
            api_key=kimi_code_api_key,
            base_url="https://api.kimi.com/coding/v1",
        )

    moonshot_api_key = os.environ.get("MOONSHOT_API_KEY")
    if moonshot_api_key:
        kimi_model_ctor = _import_kimi_model()
        return kimi_model_ctor(
            model="moonshot-v1-8k",
            api_key=moonshot_api_key,
            temperature=0,
        )

    return None


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
    judge_model = _build_metric_judge_model()
    if judge_model is None:
        return metric_ctor(threshold=threshold)
    return metric_ctor(threshold=threshold, model=judge_model)


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
