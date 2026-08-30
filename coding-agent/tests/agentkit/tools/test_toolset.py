from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

import pytest

from agentkit.directive.types import Approve, AskUser, Reject
from agentkit.observability import SpanRecord
from agentkit.plugin.registry import PluginRegistry
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.runtime.hookspecs import HOOK_SPECS
from agentkit.tools import (
    ToolCallRequest,
    ToolExecutionOptions,
    ToolExecutionResult,
    ToolSchema,
    Toolset,
    UNHANDLED_TOOL_RESULT,
)


class RecordingObservationSink:
    def __init__(self) -> None:
        self.spans: list[SpanRecord] = []

    def record_span(self, span: SpanRecord) -> None:
        self.spans.append(span)

    def record_event(self, event: Any) -> None:
        del event


class ToolContext:
    def __init__(self, sink: RecordingObservationSink) -> None:
        self.config = {"observation_sink": sink}


class SingleToolPlugin:
    state_key = "single_tool"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def hooks(self):
        return {"execute_tool": self.execute_tool}

    def execute_tool(
        self, name: str = "", arguments: dict[str, Any] | None = None, **kwargs: Any
    ) -> str | object:
        del kwargs
        self.calls.append((name, arguments or {}))
        if name != "known_tool":
            return UNHANDLED_TOOL_RESULT
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


class AsyncApprovalPlugin:
    state_key = "async_approval"

    def hooks(self):
        return {"approve_tool_call": self.approve_tool_call}

    async def approve_tool_call(self, **kwargs: Any) -> Approve:
        del kwargs
        await asyncio.sleep(0)
        return Approve()


class RaisingApprovalPlugin:
    state_key = "raising_approval"

    def hooks(self):
        return {"approve_tool_call": self.approve_tool_call}

    def approve_tool_call(self, **kwargs: Any) -> Any:
        del kwargs
        raise RuntimeError("hook exploded")


class AsyncRaisingApprovalPlugin:
    state_key = "async_raising_approval"

    def hooks(self):
        return {"approve_tool_call": self.approve_tool_call}

    async def approve_tool_call(self, **kwargs: Any) -> Any:
        del kwargs
        await asyncio.sleep(0)
        raise RuntimeError("async hook exploded")


class RaisingHookLookupRuntime:
    def get_hooks(self, hook_name: str) -> Any:
        del hook_name
        raise RuntimeError("hook lookup exploded")


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


class UnhandledSideEffectToolPlugin:
    state_key = "unhandled_side_effect_tool"

    def __init__(self) -> None:
        self.calls = 0

    def hooks(self):
        return {"execute_tool": self.execute_tool}

    def execute_tool(self, **kwargs: Any) -> object:
        del kwargs
        self.calls += 1
        return UNHANDLED_TOOL_RESULT


class FlakySecondToolPlugin:
    state_key = "flaky_second_tool"

    def __init__(self) -> None:
        self.calls = 0

    def hooks(self):
        return {"execute_tool": self.execute_tool}

    def execute_tool(self, **kwargs: Any) -> str:
        del kwargs
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("second provider transient")
        return "retried-second"


class SlowToolPlugin:
    state_key = "slow_tool"

    def hooks(self):
        return {"execute_tool": self.execute_tool}

    async def execute_tool(self, **kwargs: Any) -> str:
        del kwargs
        await asyncio.sleep(0.05)
        return "too-slow"


class NoneResultToolPlugin:
    state_key = "none_result_tool"

    def hooks(self):
        return {"execute_tool": self.execute_tool}

    def execute_tool(self, **kwargs: Any) -> None:
        del kwargs
        return None


class RaisingBatchToolPlugin:
    state_key = "raising_batch_tool"

    def hooks(self):
        return {"execute_tools_batch": self.execute_tools_batch}

    async def execute_tools_batch(self, **kwargs: Any) -> list[Any]:
        del kwargs
        await asyncio.sleep(0)
        raise RuntimeError("batch exploded")


class FlakyBatchToolPlugin:
    state_key = "flaky_batch_tool"

    def __init__(self) -> None:
        self.calls = 0

    def hooks(self):
        return {"execute_tools_batch": self.execute_tools_batch}

    async def execute_tools_batch(self, **kwargs: Any) -> list[str]:
        del kwargs
        self.calls += 1
        await asyncio.sleep(0)
        if self.calls == 1:
            raise RuntimeError("batch transient")
        return ["retried-one", "retried-two"]


class NoneBatchToolPlugin:
    state_key = "none_batch_tool"

    def hooks(self):
        return {"execute_tools_batch": self.execute_tools_batch}

    def execute_tools_batch(self, **kwargs: Any) -> None:
        del kwargs
        return None


class DirectToolPlugin:
    state_key = "direct_tool"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def hooks(self):
        return {
            "get_tools": self.get_tools,
            "execute_tool": self.execute_tool,
        }

    def get_tools(self, **kwargs: Any) -> list[ToolSchema]:
        del kwargs
        return [
            ToolSchema(
                name="direct_echo",
                description="Direct echo tool",
                parameters={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            )
        ]

    def execute_tool(
        self, name: str = "", arguments: dict[str, Any] | None = None, **kwargs: Any
    ) -> str | object:
        del kwargs
        self.calls.append((name, arguments or {}))
        if name != "direct_echo":
            return UNHANDLED_TOOL_RESULT
        return f"direct:{(arguments or {}).get('value')}"


class ConflictingDirectToolPlugin(DirectToolPlugin):
    state_key = "conflicting_direct_tool"

    def get_tools(self, **kwargs: Any) -> list[ToolSchema]:
        del kwargs
        return [
            ToolSchema(
                name="dynamic_echo",
                description="Direct conflicting echo tool",
                parameters={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            )
        ]

    def execute_tool(
        self, name: str = "", arguments: dict[str, Any] | None = None, **kwargs: Any
    ) -> str | object:
        del kwargs
        self.calls.append((name, arguments or {}))
        if name != "dynamic_echo":
            return UNHANDLED_TOOL_RESULT
        return f"direct-conflict:{(arguments or {}).get('value')}"


class ProxyToolPlugin:
    state_key = "proxy_tool"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def hooks(self):
        return {
            "get_proxy_tools": self.get_proxy_tools,
            "execute_proxy_tool": self.execute_proxy_tool,
        }

    def get_proxy_tools(self, **kwargs: Any) -> list[ToolSchema]:
        del kwargs
        return [
            ToolSchema(
                name="dynamic_echo",
                description="Dynamic echo tool",
                parameters={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            ),
            ToolSchema(
                name="dynamic_status",
                description="Dynamic status tool",
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
        ]

    def execute_proxy_tool(
        self, name: str = "", arguments: dict[str, Any] | None = None, **kwargs: Any
    ) -> str | object:
        del kwargs
        self.calls.append((name, arguments or {}))
        if name == "dynamic_echo":
            return f"proxy:{(arguments or {}).get('value')}"
        if name == "dynamic_status":
            return "proxy:ok"
        return UNHANDLED_TOOL_RESULT


class RaisingProxyToolPlugin(ProxyToolPlugin):
    state_key = "raising_proxy_tool"

    def get_proxy_tools(self, **kwargs: Any) -> list[ToolSchema]:
        del kwargs
        return [
            ToolSchema(
                name="dynamic_boom",
                description="Dynamic tool that raises",
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            )
        ]

    def execute_proxy_tool(
        self, name: str = "", arguments: dict[str, Any] | None = None, **kwargs: Any
    ) -> object:
        del arguments, kwargs
        if name == "dynamic_boom":
            raise PermissionError("target denied")
        return UNHANDLED_TOOL_RESULT


class RecordingApprovalPlugin:
    state_key = "recording_approval"

    def __init__(self, result: Any = None) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def hooks(self):
        return {"approve_tool_call": self.approve_tool_call}

    def approve_tool_call(
        self,
        tool_name: str = "",
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        del kwargs
        self.calls.append((tool_name, arguments or {}))
        return self.result


class DenyingDirectiveExecutor:
    async def execute(self, directive: Any) -> bool:
        del directive
        return False


def _runtime(*plugins: Any) -> HookRuntime:
    registry = PluginRegistry()
    for plugin in plugins:
        registry.register(plugin)
    return HookRuntime(registry)


def _runtime_with_specs(*plugins: Any) -> HookRuntime:
    registry = PluginRegistry(specs=HOOK_SPECS)
    for plugin in plugins:
        registry.register(plugin)
    return HookRuntime(registry, specs=HOOK_SPECS)


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
async def test_toolset_records_tool_call_span_without_arguments_or_result() -> None:
    plugin = SingleToolPlugin()
    sink = RecordingObservationSink()
    toolset = Toolset(runtime=_runtime(plugin))

    results = await toolset.execute_tools(
        [
            ToolCallRequest(
                tool_call_id="tc-1",
                name="known_tool",
                arguments={"secret": "do-not-record"},
            )
        ],
        ctx=ToolContext(sink),
    )

    assert results[0] == ToolExecutionResult(
        tool_call_id="tc-1",
        name="known_tool",
        result="ok:None",
    )
    assert len(sink.spans) == 1
    assert sink.spans[0].name == "tool.call"
    assert sink.spans[0].status == "ok"
    assert sink.spans[0].attributes == {
        "tool.name": "known_tool",
        "tool.call_id": "tc-1",
        "tool.missing": False,
        "tool.error": False,
    }


@pytest.mark.asyncio
async def test_toolset_records_error_tool_call_span() -> None:
    sink = RecordingObservationSink()
    toolset = Toolset(runtime=_runtime(FlakyToolPlugin()))

    results = await toolset.execute_tools(
        [ToolCallRequest(tool_call_id="tc-1", name="known_tool", arguments={})],
        ctx=ToolContext(sink),
    )

    assert results[0].is_error is True
    assert len(sink.spans) == 1
    assert sink.spans[0].name == "tool.call"
    assert sink.spans[0].status == "ok"
    assert sink.spans[0].attributes == {
        "tool.name": "known_tool",
        "tool.call_id": "tc-1",
        "tool.missing": False,
        "tool.error": True,
        "tool.error_type": "RuntimeError",
    }


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
async def test_toolset_approval_rejects_when_hook_lookup_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    toolset = Toolset(
        runtime=cast(HookRuntime, cast(object, RaisingHookLookupRuntime()))
    )

    with caplog.at_level(logging.WARNING):
        approval = await toolset.approve_tool_call(
            ToolCallRequest(tool_call_id="tc-lookup", name="bash_run", arguments={}),
            ctx=object(),
        )

    assert approval.approved is False
    assert approval.reason == "policy"
    assert "RuntimeError" in caplog.text
    assert "bash_run" in caplog.text


@pytest.mark.asyncio
async def test_toolset_approval_rejects_when_hook_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    toolset = Toolset(runtime=_runtime(RaisingApprovalPlugin()))

    with caplog.at_level(logging.WARNING):
        approval = await toolset.approve_tool_call(
            ToolCallRequest(tool_call_id="tc-hook", name="bash_run", arguments={}),
            ctx=object(),
        )

    assert approval.approved is False
    assert approval.reason == "policy"
    assert "RuntimeError" in caplog.text
    assert "bash_run" in caplog.text


@pytest.mark.asyncio
async def test_toolset_approval_rejects_when_async_hook_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    toolset = Toolset(runtime=_runtime(AsyncRaisingApprovalPlugin()))

    with caplog.at_level(logging.WARNING):
        approval = await toolset.approve_tool_call(
            ToolCallRequest(
                tool_call_id="tc-async-hook", name="bash_run", arguments={}
            ),
            ctx=object(),
        )

    assert approval.approved is False
    assert approval.reason == "policy"
    assert "RuntimeError" in caplog.text
    assert "bash_run" in caplog.text


@pytest.mark.asyncio
async def test_toolset_approval_executes_directive() -> None:
    executed: list[Approve] = []
    sink = RecordingObservationSink()

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
        ctx=ToolContext(sink),
    )

    assert approval.approved is True
    assert approval.directive is directive
    assert executed == [directive]
    assert len(sink.spans) == 1
    assert sink.spans[0].name == "approval.wait"
    assert sink.spans[0].status == "ok"
    assert sink.spans[0].attributes == {
        "tool.name": "file_read",
        "tool.call_id": "tc-1",
        "approval.directive_type": "Approve",
        "approval.approved": True,
    }


@pytest.mark.asyncio
async def test_toolset_approval_rejects_with_directive_reason_when_executor_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sink = RecordingObservationSink()

    class DirectiveExecutor:
        async def execute(self, directive: Reject) -> bool:
            del directive
            raise RuntimeError("executor exploded")

    directive = Reject(reason="blocked")
    toolset = Toolset(
        runtime=_runtime(ApprovalPlugin(result=directive)),
        directive_executor=DirectiveExecutor(),
    )

    with caplog.at_level(logging.WARNING):
        approval = await toolset.approve_tool_call(
            ToolCallRequest(tool_call_id="tc-executor", name="bash_run", arguments={}),
            ctx=ToolContext(sink),
        )

    assert approval.approved is False
    assert approval.reason == "blocked"
    assert approval.directive is directive
    assert "RuntimeError" in caplog.text
    assert "bash_run" in caplog.text
    assert len(sink.spans) == 1
    assert sink.spans[0].name == "approval.wait"
    assert sink.spans[0].status == "error"
    assert sink.spans[0].error_type == "RuntimeError"
    assert sink.spans[0].attributes == {
        "tool.name": "bash_run",
        "tool.call_id": "tc-executor",
        "approval.directive_type": "Reject",
    }


@pytest.mark.asyncio
async def test_toolset_approval_rejects_directive_when_executor_missing() -> None:
    directive = Reject(reason="blocked")
    toolset = Toolset(runtime=_runtime(ApprovalPlugin(result=directive)))

    approval = await toolset.approve_tool_call(
        ToolCallRequest(
            tool_call_id="tc-missing-executor", name="bash_run", arguments={}
        ),
        ctx=object(),
    )

    assert approval.approved is False
    assert approval.directive is directive
    assert approval.reason == "missing directive executor"


@pytest.mark.asyncio
async def test_toolset_approval_rejects_ask_user_when_executor_missing() -> None:
    directive = AskUser(question="Allow bash_run?")
    toolset = Toolset(runtime=_runtime(ApprovalPlugin(result=directive)))

    approval = await toolset.approve_tool_call(
        ToolCallRequest(
            tool_call_id="tc-missing-executor", name="bash_run", arguments={}
        ),
        ctx=object(),
    )

    assert approval.approved is False
    assert approval.directive is directive
    assert approval.reason == "missing directive executor"


@pytest.mark.asyncio
async def test_toolset_approval_awaits_async_hooks_before_directive_check() -> None:
    executed: list[Approve] = []

    class DirectiveExecutor:
        async def execute(self, directive: Approve) -> bool:
            executed.append(directive)
            return True

    toolset = Toolset(
        runtime=_runtime_with_specs(AsyncApprovalPlugin()),
        directive_executor=DirectiveExecutor(),
    )

    approval = await toolset.approve_tool_call(
        ToolCallRequest(
            tool_call_id="tc-async-approval", name="bash_run", arguments={}
        ),
        ctx=object(),
    )

    assert approval.approved is True
    assert isinstance(approval.directive, Approve)
    assert executed == [approval.directive]


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


def test_toolset_validates_json_schema_types_enums_and_ranges() -> None:
    toolset = Toolset(runtime=_runtime())
    schema = ToolSchema(
        name="configure",
        description="Configure execution",
        parameters={
            "type": "object",
            "properties": {
                "count": {"type": "integer", "minimum": 1, "maximum": 5},
                "mode": {"type": "string", "enum": ["fast", "safe"]},
            },
            "required": ["count", "mode"],
            "additionalProperties": False,
        },
    )

    wrong_type = toolset.validate_tool_call(
        ToolCallRequest(
            tool_call_id="tc-type",
            name="configure",
            arguments={"count": "not-int", "mode": "fast"},
        ),
        schemas=[schema],
    )
    bad_enum = toolset.validate_tool_call(
        ToolCallRequest(
            tool_call_id="tc-enum",
            name="configure",
            arguments={"count": 2, "mode": "turbo"},
        ),
        schemas=[schema],
    )
    too_small = toolset.validate_tool_call(
        ToolCallRequest(
            tool_call_id="tc-minimum",
            name="configure",
            arguments={"count": 0, "mode": "safe"},
        ),
        schemas=[schema],
    )
    too_large = toolset.validate_tool_call(
        ToolCallRequest(
            tool_call_id="tc-maximum",
            name="configure",
            arguments={"count": 6, "mode": "safe"},
        ),
        schemas=[schema],
    )

    assert wrong_type is not None
    assert wrong_type.message == "invalid argument count: expected integer"
    assert bad_enum is not None
    assert bad_enum.message == "invalid argument mode: expected one of fast, safe"
    assert too_small is not None
    assert too_small.message == "invalid argument count: must be >= 1"
    assert too_large is not None
    assert too_large.message == "invalid argument count: must be <= 5"


def test_toolset_validates_nested_required_arguments() -> None:
    toolset = Toolset(runtime=_runtime())
    schema = ToolSchema(
        name="deploy",
        description="Deploy a service",
        parameters={
            "type": "object",
            "properties": {
                "target": {
                    "type": "object",
                    "properties": {
                        "region": {"type": "string"},
                        "replicas": {"type": "integer"},
                    },
                    "required": ["region"],
                    "additionalProperties": False,
                }
            },
            "required": ["target"],
            "additionalProperties": False,
        },
    )

    missing_nested = toolset.validate_tool_call(
        ToolCallRequest(
            tool_call_id="tc-nested-required",
            name="deploy",
            arguments={"target": {"replicas": 2}},
        ),
        schemas=[schema],
    )
    unexpected_nested = toolset.validate_tool_call(
        ToolCallRequest(
            tool_call_id="tc-nested-extra",
            name="deploy",
            arguments={"target": {"region": "iad", "extra": True}},
        ),
        schemas=[schema],
    )

    assert missing_nested is not None
    assert missing_nested.message == "missing required argument: target.region"
    assert unexpected_nested is not None
    assert unexpected_nested.message == "unexpected argument: target.extra"


def test_toolset_collects_proxy_affordances_without_exposing_proxy_targets() -> None:
    toolset = Toolset(runtime=_runtime(DirectToolPlugin(), ProxyToolPlugin()))

    schemas = toolset.collect_schemas()

    assert [schema.name for schema in schemas] == [
        "direct_echo",
        "search_tools",
        "call_tool",
    ]


def test_toolset_warns_on_direct_and_proxy_tool_name_overlap(
    caplog: pytest.LogCaptureFixture,
) -> None:
    toolset = Toolset(
        runtime=_runtime(ConflictingDirectToolPlugin(), ProxyToolPlugin())
    )

    with caplog.at_level(logging.WARNING):
        schemas = toolset.collect_schemas()

    assert [schema.name for schema in schemas] == [
        "dynamic_echo",
        "search_tools",
        "call_tool",
    ]
    assert "direct/proxy tool name overlap" in caplog.text
    assert "dynamic_echo" in caplog.text


@pytest.mark.asyncio
async def test_toolset_search_tools_returns_matching_proxy_schemas() -> None:
    toolset = Toolset(runtime=_runtime(ProxyToolPlugin()))

    results = await toolset.execute_tools(
        [
            ToolCallRequest(
                tool_call_id="tc-search",
                name="search_tools",
                arguments={"query": "echo", "limit": 5},
            )
        ],
        ctx=object(),
    )

    assert results[0].is_error is False
    assert results[0].result == {
        "tools": [
            {
                "name": "dynamic_echo",
                "description": "Dynamic echo tool",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            }
        ],
        "count": 1,
    }


@pytest.mark.asyncio
async def test_toolset_search_tools_empty_query_lists_proxy_schemas_by_limit() -> None:
    toolset = Toolset(runtime=_runtime(ProxyToolPlugin()))

    results = await toolset.execute_tools(
        [
            ToolCallRequest(
                tool_call_id="tc-search-all",
                name="search_tools",
                arguments={"query": "", "limit": 1},
            )
        ],
        ctx=object(),
    )

    assert results[0].is_error is False
    assert results[0].result == {
        "tools": [
            {
                "name": "dynamic_echo",
                "description": "Dynamic echo tool",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            }
        ],
        "count": 1,
    }


@pytest.mark.asyncio
async def test_toolset_call_tool_validates_approves_and_executes_proxy_target() -> None:
    proxy = ProxyToolPlugin()
    approval = RecordingApprovalPlugin()
    toolset = Toolset(runtime=_runtime(proxy, approval))

    results = await toolset.execute_tools(
        [
            ToolCallRequest(
                tool_call_id="tc-call",
                name="call_tool",
                arguments={
                    "name": "dynamic_echo",
                    "arguments": {"value": "hello"},
                },
            )
        ],
        ctx=object(),
    )

    assert results[0] == ToolExecutionResult(
        tool_call_id="tc-call",
        name="call_tool",
        result="proxy:hello",
    )
    assert approval.calls == [("dynamic_echo", {"value": "hello"})]
    assert proxy.calls == [("dynamic_echo", {"value": "hello"})]


@pytest.mark.asyncio
async def test_toolset_call_tool_preserves_proxy_hook_exception_type() -> None:
    toolset = Toolset(runtime=_runtime(RaisingProxyToolPlugin()))

    results = await toolset.execute_tools(
        [
            ToolCallRequest(
                tool_call_id="tc-call-error",
                name="call_tool",
                arguments={"name": "dynamic_boom", "arguments": {}},
            )
        ],
        ctx=object(),
    )

    assert results[0].tool_call_id == "tc-call-error"
    assert results[0].name == "call_tool"
    assert isinstance(results[0].error, PermissionError)
    assert str(results[0].error) == "target denied"
    assert results[0].error_message == "Error executing tool 'call_tool': target denied"


@pytest.mark.asyncio
async def test_toolset_call_tool_rejects_invalid_proxy_arguments_before_execution() -> (
    None
):
    proxy = ProxyToolPlugin()
    approval = RecordingApprovalPlugin()
    toolset = Toolset(runtime=_runtime(proxy, approval))

    results = await toolset.execute_tools(
        [
            ToolCallRequest(
                tool_call_id="tc-call-invalid",
                name="call_tool",
                arguments={"name": "dynamic_echo", "arguments": {}},
            )
        ],
        ctx=object(),
    )

    assert results[0].tool_call_id == "tc-call-invalid"
    assert results[0].name == "call_tool"
    assert isinstance(results[0].error, ValueError)
    assert str(results[0].error) == (
        "Tool call validation failed for 'dynamic_echo': "
        "missing required argument: value"
    )
    assert approval.calls == []
    assert proxy.calls == []


@pytest.mark.asyncio
async def test_toolset_call_tool_rejects_when_proxy_target_approval_denies() -> None:
    proxy = ProxyToolPlugin()
    approval = RecordingApprovalPlugin(result=Reject(reason="blocked"))
    toolset = Toolset(
        runtime=_runtime(proxy, approval),
        directive_executor=DenyingDirectiveExecutor(),
    )

    results = await toolset.execute_tools(
        [
            ToolCallRequest(
                tool_call_id="tc-call-denied",
                name="call_tool",
                arguments={
                    "name": "dynamic_echo",
                    "arguments": {"value": "hello"},
                },
            )
        ],
        ctx=object(),
    )

    assert results[0].tool_call_id == "tc-call-denied"
    assert results[0].name == "call_tool"
    assert isinstance(results[0].error, PermissionError)
    assert str(results[0].error) == "Tool call rejected: blocked"
    assert approval.calls == [("dynamic_echo", {"value": "hello"})]
    assert proxy.calls == []


@pytest.mark.asyncio
async def test_toolset_call_tool_uses_proxy_hooks_for_conflicting_target_names() -> (
    None
):
    direct = ConflictingDirectToolPlugin()
    proxy = ProxyToolPlugin()
    toolset = Toolset(runtime=_runtime(direct, proxy))

    results = await toolset.execute_tools(
        [
            ToolCallRequest(
                tool_call_id="tc-call-conflict",
                name="call_tool",
                arguments={
                    "name": "dynamic_echo",
                    "arguments": {"value": "hello"},
                },
            )
        ],
        ctx=object(),
    )

    assert results[0] == ToolExecutionResult(
        tool_call_id="tc-call-conflict",
        name="call_tool",
        result="proxy:hello",
    )
    assert direct.calls == []
    assert proxy.calls == [("dynamic_echo", {"value": "hello"})]


@pytest.mark.asyncio
async def test_toolset_proxy_tools_bypass_batch_hooks() -> None:
    proxy = ProxyToolPlugin()
    batch = BatchToolPlugin(results=["batch-should-not-run", "batch-should-not-run"])
    toolset = Toolset(runtime=_runtime(batch, proxy))

    results = await toolset.execute_tools(
        [
            ToolCallRequest(
                tool_call_id="tc-call-1",
                name="call_tool",
                arguments={
                    "name": "dynamic_echo",
                    "arguments": {"value": "one"},
                },
            ),
            ToolCallRequest(
                tool_call_id="tc-call-2",
                name="call_tool",
                arguments={
                    "name": "dynamic_status",
                    "arguments": {},
                },
            ),
        ],
        ctx=object(),
    )

    assert results == [
        ToolExecutionResult(
            tool_call_id="tc-call-1",
            name="call_tool",
            result="proxy:one",
        ),
        ToolExecutionResult(
            tool_call_id="tc-call-2",
            name="call_tool",
            result="proxy:ok",
        ),
    ]
    assert proxy.calls == [
        ("dynamic_echo", {"value": "one"}),
        ("dynamic_status", {}),
    ]


@pytest.mark.asyncio
async def test_toolset_invokes_failing_plugin_once_without_retry() -> None:
    plugin = FlakyToolPlugin()
    toolset = Toolset(runtime=_runtime(plugin))

    results = await toolset.execute_tools(
        [ToolCallRequest(tool_call_id="tc-once", name="known_tool", arguments={})],
        ctx=object(),
    )

    assert results[0].tool_call_id == "tc-once"
    assert isinstance(results[0].error, RuntimeError)
    assert str(results[0].error) == "transient"
    assert plugin.calls == 1


@pytest.mark.asyncio
async def test_toolset_invokes_failing_host_executor_once_without_plugin_fallback() -> (
    None
):
    host_executor = FlakyToolPlugin()
    legacy_plugin = SingleToolPlugin()
    toolset = Toolset(
        runtime=_runtime(legacy_plugin),
        host_executor=host_executor,
    )

    results = await toolset.execute_tools(
        [ToolCallRequest(tool_call_id="tc-host", name="known_tool", arguments={})],
        ctx=object(),
    )

    assert results[0].tool_call_id == "tc-host"
    assert isinstance(results[0].error, RuntimeError)
    assert str(results[0].error) == "transient"
    assert host_executor.calls == 1
    assert legacy_plugin.calls == []


@pytest.mark.asyncio
async def test_toolset_falls_back_once_when_host_does_not_own_tool() -> None:
    host_executor = UnhandledSideEffectToolPlugin()
    legacy_plugin = SingleToolPlugin()
    toolset = Toolset(
        runtime=_runtime(legacy_plugin),
        host_executor=host_executor,
    )

    results = await toolset.execute_tools(
        [
            ToolCallRequest(
                tool_call_id="tc-legacy",
                name="known_tool",
                arguments={"value": "legacy"},
            )
        ],
        ctx=object(),
    )

    assert results == [
        ToolExecutionResult(
            tool_call_id="tc-legacy",
            name="known_tool",
            result="ok:legacy",
        )
    ]
    assert host_executor.calls == 1
    assert legacy_plugin.calls == [("known_tool", {"value": "legacy"})]


def test_tool_execution_options_has_no_retry_count() -> None:
    with pytest.raises(TypeError, match="max_retries"):
        ToolExecutionOptions(max_retries=1)  # type: ignore[call-arg]


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


@pytest.mark.asyncio
async def test_toolset_preserves_none_as_successful_tool_result() -> None:
    toolset = Toolset(runtime=_runtime(NoneResultToolPlugin()))

    results = await toolset.execute_tools(
        [ToolCallRequest(tool_call_id="tc-none", name="none_tool", arguments={})],
        ctx=object(),
    )

    assert results == [
        ToolExecutionResult(
            tool_call_id="tc-none",
            name="none_tool",
            result=None,
        )
    ]
    assert results[0].missing is False
    assert results[0].is_error is False


@pytest.mark.asyncio
async def test_toolset_wraps_batch_hook_exceptions_for_each_tool_call() -> None:
    toolset = Toolset(runtime=_runtime(RaisingBatchToolPlugin()))

    results = await toolset.execute_tools(
        [
            ToolCallRequest(tool_call_id="tc-batch-1", name="one", arguments={}),
            ToolCallRequest(tool_call_id="tc-batch-2", name="two", arguments={}),
        ],
        ctx=object(),
    )

    assert [result.tool_call_id for result in results] == [
        "tc-batch-1",
        "tc-batch-2",
    ]
    assert [result.name for result in results] == ["one", "two"]
    assert all(isinstance(result.error, RuntimeError) for result in results)
    assert all(result.is_error for result in results)


@pytest.mark.asyncio
async def test_toolset_invokes_failing_batch_hook_once_without_retry() -> None:
    plugin = FlakyBatchToolPlugin()
    toolset = Toolset(runtime=_runtime(plugin))

    results = await toolset.execute_tools(
        [
            ToolCallRequest(tool_call_id="tc-batch-1", name="one", arguments={}),
            ToolCallRequest(tool_call_id="tc-batch-2", name="two", arguments={}),
        ],
        ctx=object(),
    )

    assert [result.tool_call_id for result in results] == [
        "tc-batch-1",
        "tc-batch-2",
    ]
    assert all(isinstance(result.error, RuntimeError) for result in results)
    assert all(str(result.error) == "batch transient" for result in results)
    assert plugin.calls == 1


@pytest.mark.asyncio
async def test_toolset_falls_back_to_single_tool_execution_when_batch_hook_declines() -> (
    None
):
    single_tool = SingleToolPlugin()
    toolset = Toolset(runtime=_runtime(NoneBatchToolPlugin(), single_tool))

    results = await toolset.execute_tools(
        [
            ToolCallRequest(
                tool_call_id="tc-batch-1",
                name="known_tool",
                arguments={"value": "one"},
            ),
            ToolCallRequest(
                tool_call_id="tc-batch-2",
                name="known_tool",
                arguments={"value": "two"},
            ),
        ],
        ctx=object(),
    )

    assert results == [
        ToolExecutionResult(
            tool_call_id="tc-batch-1",
            name="known_tool",
            result="ok:one",
        ),
        ToolExecutionResult(
            tool_call_id="tc-batch-2",
            name="known_tool",
            result="ok:two",
        ),
    ]
    assert single_tool.calls == [
        ("known_tool", {"value": "one"}),
        ("known_tool", {"value": "two"}),
    ]
