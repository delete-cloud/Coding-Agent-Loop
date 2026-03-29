import pytest
from agentkit.tools.registry import ToolRegistry
from agentkit.tools.decorator import tool
from agentkit.errors import ToolError


@tool
def fake_read(path: str) -> str:
    """Read a file."""
    return f"contents of {path}"


@tool
def fake_write(path: str, content: str) -> str:
    """Write a file."""
    return "ok"


class TestToolRegistry:
    def test_register_tool(self):
        reg = ToolRegistry()
        reg.register(fake_read)
        assert "fake_read" in reg.names()

    def test_register_multiple(self):
        reg = ToolRegistry()
        reg.register(fake_read)
        reg.register(fake_write)
        assert set(reg.names()) == {"fake_read", "fake_write"}

    def test_duplicate_raises(self):
        reg = ToolRegistry()
        reg.register(fake_read)
        with pytest.raises(ToolError, match="already registered"):
            reg.register(fake_read)

    def test_get_tool(self):
        reg = ToolRegistry()
        reg.register(fake_read)
        fn = reg.get("fake_read")
        assert fn("test.py") == "contents of test.py"

    def test_get_missing_raises(self):
        reg = ToolRegistry()
        with pytest.raises(ToolError, match="not found"):
            reg.get("nonexistent")

    def test_schemas(self):
        reg = ToolRegistry()
        reg.register(fake_read)
        reg.register(fake_write)
        schemas = reg.schemas()
        assert len(schemas) == 2
        names = {s.name for s in schemas}
        assert names == {"fake_read", "fake_write"}

    def test_execute(self):
        reg = ToolRegistry()
        reg.register(fake_read)
        result = reg.execute("fake_read", path="/tmp/a.py")
        assert result == "contents of /tmp/a.py"

    @pytest.mark.asyncio
    async def test_execute_async(self):
        @tool
        async def async_tool(x: int) -> int:
            """Double."""
            return x * 2

        reg = ToolRegistry()
        reg.register(async_tool)
        result = await reg.execute_async("async_tool", x=5)
        assert result == 10

    def test_register_plain_function_raises(self):
        def no_decorator(x: int) -> int:
            return x

        reg = ToolRegistry()
        with pytest.raises(ToolError, match="missing @tool decorator"):
            reg.register(no_decorator)
