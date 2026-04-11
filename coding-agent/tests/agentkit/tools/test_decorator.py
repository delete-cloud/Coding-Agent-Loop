import pytest
from agentkit.tools.decorator import tool


class TestToolDecorator:
    def test_basic_decoration(self):
        @tool
        def greet(name: str) -> str:
            """Say hello to someone."""
            return f"Hello, {name}!"

        assert hasattr(greet, "_tool_schema")
        assert greet._tool_schema.name == "greet"
        assert greet._tool_schema.description == "Say hello to someone."

    def test_custom_name(self):
        @tool(name="custom_greet")
        def greet(name: str) -> str:
            """Say hello."""
            return f"Hello, {name}!"

        assert greet._tool_schema.name == "custom_greet"

    def test_custom_description(self):
        @tool(description="A custom description")
        def greet(name: str) -> str:
            """Original docstring."""
            return f"Hello, {name}!"

        assert greet._tool_schema.description == "A custom description"

    def test_function_still_callable(self):
        @tool
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        assert add(2, 3) == 5

    def test_parameters_extracted_from_annotations(self):
        @tool
        def search(query: str, limit: int = 10) -> list:
            """Search for things."""
            return []

        params = search._tool_schema.parameters
        assert "query" in params["properties"]
        assert "limit" in params["properties"]
        assert "query" in params["required"]
        assert "limit" not in params["required"]

    def test_no_docstring_uses_empty_description(self):
        @tool
        def nodoc(x: int) -> int:
            return x

        assert nodoc._tool_schema.description == ""

    def test_async_function(self):
        @tool
        async def async_read(path: str) -> str:
            """Read a file async."""
            return "content"

        assert async_read._tool_schema.name == "async_read"
        assert async_read._tool_schema.description == "Read a file async."
