import pytest
from pydantic import BaseModel
from typing import Any
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

    def test_execute_ignores_unexpected_kwargs(self):
        reg = ToolRegistry()
        reg.register(fake_read)

        result = reg.execute("fake_read", path="/tmp/a.py", ignored=True)

        assert result == "contents of /tmp/a.py"

    @pytest.mark.asyncio
    async def test_execute_async_ignores_unexpected_kwargs(self):
        @tool
        async def async_tool(x: int) -> int:
            """Double."""
            return x * 2

        reg = ToolRegistry()
        reg.register(async_tool)

        result = await reg.execute_async("async_tool", x=5, ignored=True)

        assert result == 10

    def test_register_plain_function_raises(self):
        def no_decorator(x: int) -> int:
            return x

        reg = ToolRegistry()
        with pytest.raises(ToolError, match="missing @tool decorator"):
            reg.register(no_decorator)

    def test_execute_validates_output_model(self):
        class OutputModel(BaseModel):
            value: int

        @tool(output_model=OutputModel)
        def structured_tool() -> dict[str, Any]:
            return {"value": 3}

        reg = ToolRegistry()
        reg.register(structured_tool)

        result = reg.execute("structured_tool")

        assert isinstance(result, OutputModel)
        assert result.value == 3

    def test_execute_raises_on_invalid_output_model(self):
        class OutputModel(BaseModel):
            value: int

        @tool(output_model=OutputModel)
        def bad_tool() -> dict[str, Any]:
            return {"value": "oops"}

        reg = ToolRegistry()
        reg.register(bad_tool)

        with pytest.raises(ToolError, match="output validation failed"):
            reg.execute("bad_tool")

    @pytest.mark.asyncio
    async def test_execute_async_validates_output_model(self):
        class OutputModel(BaseModel):
            value: int

        @tool(output_model=OutputModel)
        async def async_structured_tool() -> dict[str, Any]:
            return {"value": 5}

        reg = ToolRegistry()
        reg.register(async_structured_tool)

        result = await reg.execute_async("async_structured_tool")

        assert isinstance(result, OutputModel)
        assert result.value == 5

    def test_execute_without_output_model_passes_through_unchanged(self):
        @tool
        def plain_tool() -> dict[str, Any]:
            return {"value": "raw"}

        reg = ToolRegistry()
        reg.register(plain_tool)

        result = reg.execute("plain_tool")

        assert result == {"value": "raw"}

    def test_retain_keeps_only_named_tools(self):
        @tool
        def tool_a(x: int) -> int:
            """A."""
            return x

        @tool
        def tool_b(x: int) -> int:
            """B."""
            return x

        @tool
        def tool_c(x: int) -> int:
            """C."""
            return x

        reg = ToolRegistry()
        reg.register(tool_a)
        reg.register(tool_b)
        reg.register(tool_c)

        reg.retain(["tool_a", "tool_c"])

        assert set(reg.names()) == {"tool_a", "tool_c"}
        assert len(reg.schemas()) == 2

    def test_retain_raises_on_unknown_name(self):
        reg = ToolRegistry()
        reg.register(fake_read)

        with pytest.raises(ToolError, match="unknown tools"):
            reg.retain(["fake_read", "nonexistent"])
