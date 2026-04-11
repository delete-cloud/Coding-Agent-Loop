import pytest
from pydantic import BaseModel
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

    def test_output_model_attached_to_schema(self):
        class OutputModel(BaseModel):
            value: int

        @tool(output_model=OutputModel)
        def build_value(x: int) -> dict:
            return {"value": x}

        assert build_value._tool_schema.output_model is OutputModel

    def test_internal_runtime_parameters_are_hidden_from_schema(self):
        @tool
        async def runtime_bound_tool(
            goal: str, __pipeline_ctx__: object | None = None
        ) -> str:
            return goal

        params = runtime_bound_tool._tool_schema.parameters

        assert params["type"] == "object"
        assert params["additionalProperties"] is False
        assert set(params["properties"]) == {"goal"}
        assert params["required"] == ["goal"]
