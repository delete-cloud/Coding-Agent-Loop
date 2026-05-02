from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentkit.directive.types import Approve
from agentkit.plugin.registry import PluginRegistry
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.tools import (
    ToolCallRequest,
    ToolExecutionOptions,
    ToolExecutionResult,
    ToolSchema,
    Toolset,
)


class SingleToolPlugin:
    state_key = "single_tool"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def hooks(self):
        return {"execute_tool": self.execute_tool}

    def execute_tool(
        self, name: str = "", arguments: dict[str, Any] | None = None, **kwargs: Any
    ) -> str | None:
        del kwargs
        self.calls.append((name, arguments or {}))
        if name != "known_tool":
            return None
        return f"ok:{(arguments or {}).get('value')}"


class BatchToolPlugin:
    state_key = "batch_tool"

    def __init__(self, results: list[Any]) -> None:
        self.results = results

    def hooks(self):
        return {"execute_tools_batch": self.execute_tools_batch}

    def execute_tools_batch(self, tool_calls=None, **kwargs: Any):
        del tool_calls, kwargs
        return self.results


class ApprovalPlugin:
    state_key = "approval"

    def __init__(self, result: Any) -> None:
        self.result = result

    def hooks(self):
        return {"approve_tool_call": self.approve_tool_call}

    def approve_tool_call(self, **kwargs: Any) -> Any:
        del kwargs
        return self.result


class FlakyToolPlugin:
    state_key = "flaky_tool"

    def __init__(self) -> None:
        self.calls = 0

    def hooks(self):
        return {"execute_tool": self.execute_tool}

    def execute_tool(self, **kwargs: Any) -> str:
        del kwargs
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient")
        return "retried"


class SlowToolPlugin:
    state_key = "slow_tool"

    def hooks(self):
        return {"execute_tool": self.execute_tool}

    async def execute_tool(self, **kwargs: Any) -> str:
        del kwargs
        await asyncio.sleep(0.05)
        return "too-slow"


def _runtime(*plugins: Any) -> HookRuntime:
    registry = PluginRegistry()
    for plugin in plugins:
        registry.register(plugin)
    return HookRuntime(registry)


@pytest.mark.asyncio
async def test_toolset_executes_single_tools_into_result_envelopes() -> None:
    plugin = SingleToolPlugin()
    toolset = Toolset(runtime=_runtime(plugin))

    results = await toolset.execute_tools(
        [
            ToolCallRequest(
                tool_call_id="tc-1",
                name="known_tool",
                arguments={"value": "a"},
            )
        ],
        ctx=object(),
    )

    assert results == [
        ToolExecutionResult(
            tool_call_id="tc-1",
            name="known_tool",
            result="ok:a",
        )
    ]
    assert plugin.calls == [("known_tool", {"value": "a"})]


@pytest.mark.asyncio
async def test_toolset_marks_missing_tool_without_raising() -> None:
    toolset = Toolset(runtime=_runtime(SingleToolPlugin()))

    results = await toolset.execute_tools(
        [
            ToolCallRequest(
                tool_call_id="tc-missing",
                name="missing_tool",
                arguments={},
            )
        ],
        ctx=object(),
    )

    assert results[0].missing is True
    assert results[0].is_error is True
    assert (
        results[0].error_message
        == "Error executing tool 'missing_tool': tool 'missing_tool' not found"
    )


@pytest.mark.asyncio
async def test_toolset_rejects_batch_result_count_mismatch() -> None:
    toolset = Toolset(runtime=_runtime(BatchToolPlugin(results=["only-one"])))

    with pytest.raises(ValueError, match="execute_tools_batch returned 1 results"):
        await toolset.execute_tools(
            [
                ToolCallRequest(tool_call_id="tc-1", name="one", arguments={}),
                ToolCallRequest(tool_call_id="tc-2", name="two", arguments={}),
            ],
            ctx=object(),
        )


@pytest.mark.asyncio
async def test_toolset_approval_rejects_non_directive_fail_closed() -> None:
    toolset = Toolset(runtime=_runtime(ApprovalPlugin(result={"approved": True})))

    approval = await toolset.approve_tool_call(
        ToolCallRequest(tool_call_id="tc-1", name="bash_run", arguments={}),
        ctx=object(),
    )

    assert approval.approved is False
    assert approval.reason == "policy"


@pytest.mark.asyncio
async def test_toolset_approval_executes_directive() -> None:
    executed: list[Approve] = []

    class DirectiveExecutor:
        async def execute(self, directive: Approve) -> bool:
            executed.append(directive)
            return True

    directive = Approve()
    toolset = Toolset(
        runtime=_runtime(ApprovalPlugin(result=directive)),
        directive_executor=DirectiveExecutor(),
    )

    approval = await toolset.approve_tool_call(
        ToolCallRequest(tool_call_id="tc-1", name="file_read", arguments={}),
        ctx=object(),
    )

    assert approval.approved is True
    assert approval.directive is directive
    assert executed == [directive]


def test_toolset_validates_required_and_unknown_arguments() -> None:
    toolset = Toolset(runtime=_runtime())
    schema = ToolSchema(
        name="file_read",
        description="Read a file",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    )

    missing = toolset.validate_tool_call(
        ToolCallRequest(tool_call_id="tc-1", name="file_read", arguments={}),
        schemas=[schema],
    )
    extra = toolset.validate_tool_call(
        ToolCallRequest(
            tool_call_id="tc-2",
            name="file_read",
            arguments={"path": "a.txt", "extra": True},
        ),
        schemas=[schema],
    )

    assert missing is not None
    assert missing.message == "missing required argument: path"
    assert extra is not None
    assert extra.message == "unexpected argument: extra"


@pytest.mark.asyncio
async def test_toolset_retries_transient_tool_execution_failures() -> None:
    plugin = FlakyToolPlugin()
    toolset = Toolset(runtime=_runtime(plugin))

    results = await toolset.execute_tools(
        [ToolCallRequest(tool_call_id="tc-retry", name="known_tool", arguments={})],
        ctx=object(),
        options=ToolExecutionOptions(max_retries=1),
    )

    assert results == [
        ToolExecutionResult(
            tool_call_id="tc-retry",
            name="known_tool",
            result="retried",
        )
    ]
    assert plugin.calls == 2


@pytest.mark.asyncio
async def test_toolset_wraps_tool_execution_timeouts_as_errors() -> None:
    toolset = Toolset(runtime=_runtime(SlowToolPlugin()))

    results = await toolset.execute_tools(
        [ToolCallRequest(tool_call_id="tc-timeout", name="slow_tool", arguments={})],
        ctx=object(),
        options=ToolExecutionOptions(timeout_seconds=0.001),
    )

    assert results[0].tool_call_id == "tc-timeout"
    assert results[0].name == "slow_tool"
    assert isinstance(results[0].error, TimeoutError)
    assert results[0].is_error is True
