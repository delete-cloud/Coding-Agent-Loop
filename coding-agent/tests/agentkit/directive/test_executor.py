import pytest
from unittest.mock import AsyncMock, MagicMock
from agentkit.directive.executor import DirectiveExecutor
from agentkit.directive.types import Approve, Reject, AskUser, Checkpoint, MemoryRecord


class TestDirectiveExecutor:
    @pytest.fixture
    def executor(self):
        return DirectiveExecutor()

    @pytest.mark.asyncio
    async def test_approve_returns_true(self, executor):
        result = await executor.execute(Approve())
        assert result is True

    @pytest.mark.asyncio
    async def test_reject_returns_false(self, executor):
        result = await executor.execute(Reject(reason="not allowed"))
        assert result is False

    @pytest.mark.asyncio
    async def test_ask_user_with_handler(self):
        async def user_handler(question: str) -> bool:
            return True

        executor = DirectiveExecutor(ask_user_handler=user_handler)
        result = await executor.execute(AskUser(question="Allow?"))
        assert result is True

    @pytest.mark.asyncio
    async def test_ask_user_without_handler_defaults_reject(self):
        executor = DirectiveExecutor()
        result = await executor.execute(AskUser(question="Allow?"))
        assert result is False

    @pytest.mark.asyncio
    async def test_checkpoint_calls_storage(self):
        storage_handler = AsyncMock()
        executor = DirectiveExecutor(checkpoint_handler=storage_handler)
        await executor.execute(Checkpoint(plugin_id="memory", state={"key": "val"}))
        storage_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_memory_record_calls_handler(self):
        memory_handler = AsyncMock()
        executor = DirectiveExecutor(memory_handler=memory_handler)
        record = MemoryRecord(summary="test", tags=["a"], importance=0.5)
        await executor.execute(record)
        memory_handler.assert_called_once_with(record)

    @pytest.mark.asyncio
    async def test_unknown_directive_raises(self, executor):
        from agentkit.directive.types import Directive

        class UnknownDirective(Directive):
            kind: str = "unknown"

        with pytest.raises(ValueError, match="unknown directive"):
            await executor.execute(UnknownDirective())
