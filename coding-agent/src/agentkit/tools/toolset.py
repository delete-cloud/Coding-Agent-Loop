"""Generic tool governance boundary for agentkit runtimes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from agentkit.directive.types import Directive
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.tools.schema import ToolSchema

logger = logging.getLogger(__name__)


class _UnhandledToolResult:
    def __repr__(self) -> str:
        return "UNHANDLED_TOOL_RESULT"


UNHANDLED_TOOL_RESULT = _UnhandledToolResult()

SEARCH_TOOLS_NAME = "search_tools"
CALL_TOOL_NAME = "call_tool"
_PROXY_AFFORDANCE_NAMES = frozenset({SEARCH_TOOLS_NAME, CALL_TOOL_NAME})


class ToolProvider(Protocol):
    """Provider contract for components that expose executable tools."""

    def get_tools(self, **kwargs: Any) -> list[ToolSchema]: ...

    def execute_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        **kwargs: Any,
    ) -> Any:
        """Return a tool result, or UNHANDLED_TOOL_RESULT before side effects.

        Returning UNHANDLED_TOOL_RESULT means the provider does not own this tool
        call and must not perform I/O or mutate state. Toolset retries are scoped
        to the provider hook that raised, so earlier unhandled providers are not
        called again for the same retry cycle.
        """
        ...


class ProxyToolProvider(Protocol):
    """Provider contract for dynamic tools hidden behind proxy affordances."""

    def get_proxy_tools(self, **kwargs: Any) -> list[ToolSchema]: ...

    def execute_proxy_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        **kwargs: Any,
    ) -> Any:
        """Return a proxied tool result, or UNHANDLED_TOOL_RESULT before side effects."""
        ...


class ToolApprovalPolicy(Protocol):
    """Approval contract for generic tool policy implementations."""

    def approve_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        **kwargs: Any,
    ) -> Directive: ...


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    """A model-requested tool call after provider normalization."""

    tool_call_id: str
    name: str
    arguments: dict[str, Any]

    def to_hook_payload(self) -> dict[str, Any]:
        """Return the shape expected by existing execute_tools_batch hooks."""
        return {"name": self.name, "arguments": self.arguments}


@dataclass(frozen=True, slots=True)
class ToolValidationError:
    """Schema validation failure for a tool call."""

    tool_call_id: str
    name: str
    message: str


@dataclass(frozen=True, slots=True)
class ToolApprovalResult:
    """Approval decision for a tool call."""

    approved: bool
    reason: str = ""
    directive: Directive | None = None


@dataclass(frozen=True, slots=True)
class ToolExecutionOptions:
    """Execution wrapper knobs; defaults preserve legacy behavior."""

    timeout_seconds: float | None = None
    max_retries: int = 0

    def __post_init__(self) -> None:
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative")


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    """Envelope for one executed tool call."""

    tool_call_id: str
    name: str
    result: Any = None
    error: Exception | None = None
    missing: bool = False

    @property
    def is_error(self) -> bool:
        return self.error is not None or self.missing

    @property
    def error_message(self) -> str:
        if self.error is not None:
            return f"Error executing tool '{self.name}': {self.error}"
        if self.missing:
            return f"Error executing tool '{self.name}': tool '{self.name}' not found"
        return ""


class Toolset:
    """Coordinates tool schema validation, approval, and execution hooks."""

    def __init__(
        self,
        *,
        runtime: HookRuntime,
        directive_executor: Any = None,
    ) -> None:
        self._runtime = runtime
        self._directive_executor = directive_executor

    def collect_schemas(self) -> list[ToolSchema]:
        """Collect tool schemas from registered tool providers."""
        direct_schemas = self._collect_schemas_from_hook("get_tools")
        proxy_schemas = self._collect_schemas_from_hook("get_proxy_tools")
        if not proxy_schemas:
            return direct_schemas
        overlap = {schema.name for schema in direct_schemas} & {
            schema.name for schema in proxy_schemas
        }
        if overlap:
            logger.warning(
                "direct/proxy tool name overlap: %s",
                ", ".join(sorted(overlap)),
            )
        return [
            *direct_schemas,
            self._search_tools_schema(),
            self._call_tool_schema(),
        ]

    def is_proxy_affordance(self, tool_name: str) -> bool:
        """Return whether the tool is a proxy affordance, not a target tool."""
        return tool_name in _PROXY_AFFORDANCE_NAMES

    def _collect_schemas_from_hook(self, hook_name: str) -> list[ToolSchema]:
        tool_lists = self._runtime.call_many(hook_name)
        schemas: list[ToolSchema] = []
        for tool_list in tool_lists:
            if isinstance(tool_list, list):
                schemas.extend(tool_list)
            else:
                schemas.append(tool_list)
        return schemas

    def _proxy_schemas(self) -> list[ToolSchema]:
        return self._collect_schemas_from_hook("get_proxy_tools")

    def _search_tools_schema(self) -> ToolSchema:
        return ToolSchema(
            name=SEARCH_TOOLS_NAME,
            description=(
                "Search dynamically available tools by name and description. "
                "Use call_tool with the returned name to execute one."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search text matched against tool names and descriptions.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of matches to return.",
                        "minimum": 1,
                        "maximum": 50,
                    },
                },
                "additionalProperties": False,
            },
        )

    def _call_tool_schema(self) -> ToolSchema:
        return ToolSchema(
            name=CALL_TOOL_NAME,
            description=(
                "Call a dynamic tool returned by search_tools. The nested tool "
                "call is validated and approved before execution."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the dynamic tool to call.",
                    },
                    "arguments": {
                        "type": "object",
                        "description": "Arguments for the dynamic tool.",
                    },
                },
                "required": ["name", "arguments"],
                "additionalProperties": False,
            },
        )

    def validate_tool_call(
        self,
        call: ToolCallRequest,
        *,
        schemas: Sequence[ToolSchema],
    ) -> ToolValidationError | None:
        """Validate model-provided arguments against the known tool schema."""
        schema = next((item for item in schemas if item.name == call.name), None)
        if schema is None:
            return None

        message = self._validate_schema_value(
            call.arguments,
            schema.parameters,
            path=(),
        )
        if message is None:
            return None
        return ToolValidationError(
            tool_call_id=call.tool_call_id,
            name=call.name,
            message=message,
        )

    def _validate_schema_value(
        self,
        value: Any,
        schema: Mapping[str, Any],
        *,
        path: tuple[str, ...],
    ) -> str | None:
        expected_type = schema.get("type")
        if not self._matches_json_type(value, expected_type):
            return (
                f"invalid argument {self._format_path(path)}: "
                f"expected {self._format_expected_type(expected_type)}"
            )

        enum_values = schema.get("enum")
        if isinstance(enum_values, list) and value not in enum_values:
            return (
                f"invalid argument {self._format_path(path)}: "
                f"expected one of {', '.join(str(item) for item in enum_values)}"
            )

        if isinstance(value, int | float) and not isinstance(value, bool):
            minimum = schema.get("minimum")
            if isinstance(minimum, int | float) and value < minimum:
                return f"invalid argument {self._format_path(path)}: must be >= {minimum}"
            maximum = schema.get("maximum")
            if isinstance(maximum, int | float) and value > maximum:
                return f"invalid argument {self._format_path(path)}: must be <= {maximum}"

        if isinstance(value, str):
            min_length = schema.get("minLength")
            if isinstance(min_length, int) and len(value) < min_length:
                return (
                    f"invalid argument {self._format_path(path)}: "
                    f"length must be >= {min_length}"
                )
            max_length = schema.get("maxLength")
            if isinstance(max_length, int) and len(value) > max_length:
                return (
                    f"invalid argument {self._format_path(path)}: "
                    f"length must be <= {max_length}"
                )

        if isinstance(value, dict):
            return self._validate_object_schema(value, schema, path=path)

        if isinstance(value, list):
            return self._validate_array_schema(value, schema, path=path)

        return None

    def _validate_object_schema(
        self,
        value: dict[str, Any],
        schema: Mapping[str, Any],
        *,
        path: tuple[str, ...],
    ) -> str | None:
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return None

        required = schema.get("required", [])
        if isinstance(required, list):
            for name in required:
                if isinstance(name, str) and name not in value:
                    return f"missing required argument: {self._format_path((*path, name))}"

        if schema.get("additionalProperties") is False:
            allowed = set(properties)
            unexpected = sorted(set(value) - allowed)
            if unexpected:
                return f"unexpected argument: {self._format_path((*path, unexpected[0]))}"

        for name, child_schema in properties.items():
            if name not in value or not isinstance(name, str):
                continue
            if not isinstance(child_schema, Mapping):
                continue
            message = self._validate_schema_value(
                value[name],
                child_schema,
                path=(*path, name),
            )
            if message is not None:
                return message

        return None

    def _validate_array_schema(
        self,
        value: list[Any],
        schema: Mapping[str, Any],
        *,
        path: tuple[str, ...],
    ) -> str | None:
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(value) < min_items:
            return (
                f"invalid argument {self._format_path(path)}: "
                f"items length must be >= {min_items}"
            )
        max_items = schema.get("maxItems")
        if isinstance(max_items, int) and len(value) > max_items:
            return (
                f"invalid argument {self._format_path(path)}: "
                f"items length must be <= {max_items}"
            )

        items_schema = schema.get("items")
        if not isinstance(items_schema, Mapping):
            return None

        for index, item in enumerate(value):
            message = self._validate_schema_value(
                item,
                items_schema,
                path=(*path, str(index)),
            )
            if message is not None:
                return message

        return None

    def _matches_json_type(self, value: Any, expected_type: Any) -> bool:
        if expected_type is None:
            return True
        if isinstance(expected_type, list):
            return any(self._matches_json_type(value, item) for item in expected_type)
        if not isinstance(expected_type, str):
            return True
        if expected_type == "string":
            return isinstance(value, str)
        if expected_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected_type == "number":
            return isinstance(value, int | float) and not isinstance(value, bool)
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type == "object":
            return isinstance(value, dict)
        if expected_type == "array":
            return isinstance(value, list)
        if expected_type == "null":
            return value is None
        return True

    def _format_expected_type(self, expected_type: Any) -> str:
        if isinstance(expected_type, list):
            return " or ".join(str(item) for item in expected_type)
        return str(expected_type)

    def _format_path(self, path: tuple[str, ...]) -> str:
        return ".".join(path) if path else "<root>"

    async def approve_tool_call(
        self,
        call: ToolCallRequest,
        *,
        ctx: Any,
    ) -> ToolApprovalResult:
        """Run generic approval policy hooks for a tool call."""
        try:
            hooks = self._runtime.get_hooks("approve_tool_call")
        except Exception as exc:
            logger.warning(
                "approve_tool_call hook lookup failed for tool %r, rejecting (fail-closed): %s: %s",
                call.name,
                type(exc).__name__,
                exc,
            )
            return ToolApprovalResult(approved=False, reason="policy")

        for hook in hooks:
            try:
                directive = hook(
                    tool_name=call.name,
                    arguments=call.arguments,
                    ctx=ctx,
                )
                directive = await self._await_if_needed(
                    directive,
                    timeout_seconds=None,
                )
            except Exception as exc:
                logger.warning(
                    "approve_tool_call hook failed for tool %r, rejecting (fail-closed): %s: %s",
                    call.name,
                    type(exc).__name__,
                    exc,
                )
                return ToolApprovalResult(approved=False, reason="policy")
            if directive is None:
                continue
            if not isinstance(directive, Directive):
                logger.warning(
                    "approve_tool_call returned non-Directive type %s for tool %r, rejecting (fail-closed)",
                    type(directive).__name__,
                    call.name,
                )
                return ToolApprovalResult(approved=False, reason="policy")
            if self._directive_executor is None:
                return ToolApprovalResult(
                    approved=False,
                    reason="missing directive executor",
                    directive=directive,
                )
            reason = str(getattr(directive, "reason", "policy"))
            try:
                approved = await self._directive_executor.execute(directive)
            except Exception as exc:
                logger.warning(
                    "directive_executor failed for tool %r, rejecting (fail-closed): %s: %s",
                    call.name,
                    type(exc).__name__,
                    exc,
                )
                return ToolApprovalResult(
                    approved=False,
                    reason=reason,
                    directive=directive,
                )
            return ToolApprovalResult(
                approved=bool(approved),
                reason=reason,
                directive=directive,
            )
        return ToolApprovalResult(approved=True)

    async def execute_tools(
        self,
        calls: Sequence[ToolCallRequest],
        *,
        ctx: Any,
        options: ToolExecutionOptions | None = None,
    ) -> list[ToolExecutionResult]:
        """Execute tool calls through batch hooks or per-tool fallback hooks."""
        resolved_options = options or ToolExecutionOptions()
        if len(calls) > 1 and not any(
            call.name in _PROXY_AFFORDANCE_NAMES for call in calls
        ):
            batch_results = await self._execute_batch(
                calls,
                ctx=ctx,
                options=resolved_options,
            )
            if batch_results is not None:
                return batch_results

        return [
            await self._execute_one(call, ctx=ctx, options=resolved_options)
            for call in calls
        ]

    async def _execute_batch(
        self,
        calls: Sequence[ToolCallRequest],
        *,
        ctx: Any,
        options: ToolExecutionOptions,
    ) -> list[ToolExecutionResult] | None:
        hooks = self._runtime.get_hooks("execute_tools_batch")
        if not hooks:
            return None

        raw_payload = [call.to_hook_payload() for call in calls]
        for hook in hooks:
            raw_results: Any = None
            for attempt in range(options.max_retries + 1):
                try:
                    raw_results = hook(tool_calls=raw_payload, ctx=ctx)
                    raw_results = await self._await_if_needed(
                        raw_results,
                        timeout_seconds=options.timeout_seconds,
                    )
                    break
                except Exception as exc:
                    if attempt == options.max_retries:
                        return self._error_results(calls, exc)

            if raw_results is None:
                continue
            if not isinstance(raw_results, list):
                return self._error_results(
                    calls,
                    TypeError(
                        "execute_tools_batch returned "
                        f"{type(raw_results).__name__}, expected list"
                    ),
                )
            if len(raw_results) != len(calls):
                raise ValueError(
                    "execute_tools_batch returned "
                    f"{len(raw_results)} results for {len(calls)} tool calls"
                )
            return [
                self._envelope_raw_result(call, result)
                for call, result in zip(calls, raw_results, strict=True)
            ]

        return None

    async def _execute_one(
        self,
        call: ToolCallRequest,
        *,
        ctx: Any,
        options: ToolExecutionOptions,
    ) -> ToolExecutionResult:
        if call.name == SEARCH_TOOLS_NAME:
            return self._execute_search_tools(call)
        if call.name == CALL_TOOL_NAME:
            return await self._execute_call_tool(call, ctx=ctx, options=options)
        return await self._execute_one_from_hook(
            "execute_tool",
            call,
            ctx=ctx,
            options=options,
        )

    async def _execute_one_from_hook(
        self,
        hook_name: str,
        call: ToolCallRequest,
        *,
        ctx: Any,
        options: ToolExecutionOptions,
    ) -> ToolExecutionResult:
        for hook in self._runtime.get_hooks(hook_name):
            result: Any = UNHANDLED_TOOL_RESULT
            for attempt in range(options.max_retries + 1):
                try:
                    result = hook(
                        name=call.name,
                        arguments=call.arguments,
                        ctx=ctx,
                    )
                    result = await self._await_if_needed(
                        result,
                        timeout_seconds=options.timeout_seconds,
                    )
                    break
                except Exception as exc:
                    if attempt == options.max_retries:
                        return ToolExecutionResult(
                            tool_call_id=call.tool_call_id,
                            name=call.name,
                            error=exc,
                        )
            if result is UNHANDLED_TOOL_RESULT:
                continue
            return self._envelope_raw_result(call, result)

        return self._envelope_raw_result(call, UNHANDLED_TOOL_RESULT)

    def _execute_search_tools(self, call: ToolCallRequest) -> ToolExecutionResult:
        query_value = call.arguments.get("query", "")
        if not isinstance(query_value, str):
            return ToolExecutionResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                error=TypeError("search_tools.query must be a string"),
            )
        limit_value = call.arguments.get("limit", 20)
        if (
            isinstance(limit_value, bool)
            or not isinstance(limit_value, int)
            or limit_value < 1
            or limit_value > 50
        ):
            return ToolExecutionResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                error=ValueError("search_tools.limit must be an integer from 1 to 50"),
            )

        terms = [
            term
            for term in query_value.strip().casefold().split()
            if term
        ]
        matches: list[ToolSchema] = []
        for schema in self._proxy_schemas():
            haystack = f"{schema.name} {schema.description}".casefold()
            if terms and not all(term in haystack for term in terms):
                continue
            matches.append(schema)
            if len(matches) >= limit_value:
                break

        return ToolExecutionResult(
            tool_call_id=call.tool_call_id,
            name=call.name,
            result={
                "tools": [self._proxy_schema_descriptor(schema) for schema in matches],
                "count": len(matches),
            },
        )

    def _proxy_schema_descriptor(self, schema: ToolSchema) -> dict[str, Any]:
        return {
            "name": schema.name,
            "description": schema.description,
            "parameters": schema.parameters,
        }

    async def _execute_call_tool(
        self,
        call: ToolCallRequest,
        *,
        ctx: Any,
        options: ToolExecutionOptions,
    ) -> ToolExecutionResult:
        target_name = call.arguments.get("name")
        if not isinstance(target_name, str) or not target_name:
            return ToolExecutionResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                error=ValueError("call_tool.name must be a non-empty string"),
            )
        target_arguments = call.arguments.get("arguments")
        if not isinstance(target_arguments, dict):
            return ToolExecutionResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                error=TypeError("call_tool.arguments must be an object"),
            )

        proxy_schemas = self._proxy_schemas()
        target_schema = next(
            (schema for schema in proxy_schemas if schema.name == target_name),
            None,
        )
        if target_schema is None:
            return ToolExecutionResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                error=LookupError(f"proxied tool '{target_name}' not found"),
            )

        target_call = ToolCallRequest(
            tool_call_id=f"{call.tool_call_id}:{target_name}",
            name=target_schema.name,
            arguments=dict(target_arguments),
        )
        validation_error = self.validate_tool_call(
            target_call,
            schemas=proxy_schemas,
        )
        if validation_error is not None:
            return ToolExecutionResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                error=ValueError(
                    f"Tool call validation failed for {target_name!r}: "
                    f"{validation_error.message}"
                ),
            )

        approval = await self.approve_tool_call(target_call, ctx=ctx)
        if not approval.approved:
            return ToolExecutionResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                error=PermissionError(
                    f"Tool call rejected: {approval.reason or 'policy'}"
                ),
            )

        target_result = await self._execute_one_from_hook(
            "execute_proxy_tool",
            target_call,
            ctx=ctx,
            options=options,
        )
        if target_result.is_error:
            return ToolExecutionResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                error=target_result.error
                or RuntimeError(target_result.error_message),
            )

        return ToolExecutionResult(
            tool_call_id=call.tool_call_id,
            name=call.name,
            result=target_result.result,
        )

    def _envelope_raw_result(
        self,
        call: ToolCallRequest,
        result: Any,
    ) -> ToolExecutionResult:
        if isinstance(result, Exception):
            return ToolExecutionResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                error=result,
            )
        if result is UNHANDLED_TOOL_RESULT:
            return ToolExecutionResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                missing=True,
            )
        return ToolExecutionResult(
            tool_call_id=call.tool_call_id,
            name=call.name,
            result=result,
        )

    def _error_results(
        self,
        calls: Sequence[ToolCallRequest],
        error: Exception,
    ) -> list[ToolExecutionResult]:
        return [
            ToolExecutionResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                error=error,
            )
            for call in calls
        ]

    async def _await_if_needed(
        self,
        value: Any,
        *,
        timeout_seconds: float | None,
    ) -> Any:
        if not isinstance(value, Awaitable):
            return value
        if timeout_seconds is None:
            return await value
        return await asyncio.wait_for(value, timeout=timeout_seconds)
