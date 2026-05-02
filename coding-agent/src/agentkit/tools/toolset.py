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


class ToolProvider(Protocol):
    """Provider contract for components that expose executable tools."""

    def get_tools(self, **kwargs: Any) -> list[ToolSchema]: ...

    def execute_tool(
        self,
        name: str = "",
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Return a tool result, or UNHANDLED_TOOL_RESULT for unknown tools."""
        ...


class ToolApprovalPolicy(Protocol):
    """Approval contract for generic tool policy implementations."""

    def approve_tool_call(
        self,
        tool_name: str = "",
        arguments: dict[str, Any] | None = None,
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
        tool_lists = self._runtime.call_many("get_tools")
        schemas: list[ToolSchema] = []
        for tool_list in tool_lists:
            if isinstance(tool_list, list):
                schemas.extend(tool_list)
            else:
                schemas.append(tool_list)
        return schemas

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
        for hook in self._runtime.get_hooks("approve_tool_call"):
            directive = hook(
                tool_name=call.name,
                arguments=call.arguments,
                ctx=ctx,
            )
            directive = await self._await_if_needed(
                directive,
                timeout_seconds=None,
            )
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
                return ToolApprovalResult(approved=True, directive=directive)
            approved = await self._directive_executor.execute(directive)
            reason = str(getattr(directive, "reason", "policy"))
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
        if len(calls) > 1:
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
            try:
                raw_results = hook(tool_calls=raw_payload, ctx=ctx)
                raw_results = await self._await_if_needed(
                    raw_results,
                    timeout_seconds=options.timeout_seconds,
                )
            except Exception as exc:
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
        remaining_attempts = options.max_retries + 1
        while remaining_attempts > 0:
            remaining_attempts -= 1
            try:
                for hook in self._runtime.get_hooks("execute_tool"):
                    result = hook(
                        name=call.name,
                        arguments=call.arguments,
                        ctx=ctx,
                    )
                    result = await self._await_if_needed(
                        result,
                        timeout_seconds=options.timeout_seconds,
                    )
                    if result is UNHANDLED_TOOL_RESULT:
                        continue
                    return self._envelope_raw_result(call, result)
                return self._envelope_raw_result(call, UNHANDLED_TOOL_RESULT)
            except Exception as exc:
                if remaining_attempts == 0:
                    return ToolExecutionResult(
                        tool_call_id=call.tool_call_id,
                        name=call.name,
                        error=exc,
                    )

        raise RuntimeError("unreachable")

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
