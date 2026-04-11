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
        if isinstance(directive, Approve):
            return True
        elif isinstance(directive, Reject):
            logger.info("Rejected: %s", directive.reason)
            return False
        elif isinstance(directive, AskUser):
            if self._ask_user is not None:
                return await self._ask_user(directive.question)
            logger.warning("No ask_user handler — defaulting to reject")
            return False
        elif isinstance(directive, Checkpoint):
            if self._checkpoint is not None:
                await self._checkpoint(directive)
            else:
                logger.debug("No checkpoint handler — skipping")
            return None
        elif isinstance(directive, MemoryRecord):
            if self._memory is not None:
                await self._memory(directive)
            else:
                logger.debug("No memory handler — skipping")
            return None
        else:
            raise ValueError(f"unknown directive kind: {directive.kind}")
