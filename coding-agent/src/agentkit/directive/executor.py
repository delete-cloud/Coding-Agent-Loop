"""DirectiveExecutor — dispatches Directive structs to side effects."""

from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine

from agentkit.directive.types import (
    Approve,
    AskUser,
    Checkpoint,
    Directive,
    MemoryRecord,
    Reject,
)

logger = logging.getLogger(__name__)

try:
    from agentkit.tracing import get_tracer as _get_tracer

    _tracer = _get_tracer("agentkit.directive")
except Exception:
    _tracer = None

AsyncHandler = Callable[..., Coroutine[Any, Any, Any]]


class DirectiveExecutor:
    """Executes Directive structs by dispatching to registered handlers."""

    def __init__(
        self,
        ask_user_handler: AsyncHandler | None = None,
        checkpoint_handler: AsyncHandler | None = None,
        memory_handler: AsyncHandler | None = None,
    ) -> None:
        self._ask_user = ask_user_handler
        self._checkpoint = checkpoint_handler
        self._memory = memory_handler

    async def execute(self, directive: Directive) -> Any:
        directive_type = type(directive).__name__
        if isinstance(directive, Approve):
            result = True
            if _tracer is not None:
                _tracer.info(
                    "directive_execute", directive_type=directive_type, result=result
                )
            return result
        elif isinstance(directive, Reject):
            logger.info("Rejected: %s", directive.reason)
            if _tracer is not None:
                _tracer.info(
                    "directive_execute",
                    directive_type=directive_type,
                    reason=directive.reason,
                    result=False,
                )
            return False
        elif isinstance(directive, AskUser):
            if self._ask_user is not None:
                result = await self._ask_user(directive.question, directive.metadata)
                if _tracer is not None:
                    _tracer.info(
                        "directive_execute",
                        directive_type=directive_type,
                        handler_present=True,
                        result=result,
                    )
                return result
            logger.warning("No ask_user handler — defaulting to reject")
            if _tracer is not None:
                _tracer.info(
                    "directive_execute",
                    directive_type=directive_type,
                    handler_present=False,
                    result=False,
                )
            return False
        elif isinstance(directive, Checkpoint):
            if self._checkpoint is not None:
                await self._checkpoint(directive)
            else:
                logger.debug("No checkpoint handler — skipping")
            if _tracer is not None:
                _tracer.info(
                    "directive_execute",
                    directive_type=directive_type,
                    handler_present=self._checkpoint is not None,
                )
            return None
        elif isinstance(directive, MemoryRecord):
            if self._memory is not None:
                await self._memory(directive)
            else:
                logger.debug("No memory handler — skipping")
            if _tracer is not None:
                _tracer.info(
                    "directive_execute",
                    directive_type=directive_type,
                    handler_present=self._memory is not None,
                )
            return None
        else:
            raise ValueError(f"unknown directive kind: {directive.kind}")
