import pytest
from agentkit.tools.schema import ToolSchema


class TestToolSchema:
    def test_create_schema(self):
        schema = ToolSchema(
            name="file_read",
            description="Read a file",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        )
        assert schema.name == "file_read"
        assert schema.description == "Read a file"
        assert schema.parameters["required"] == ["path"]

    def test_to_openai_format(self):
        schema = ToolSchema(
            name="bash",
            description="Run a command",
            parameters={"type": "object", "properties": {"cmd": {"type": "string"}}},
        )
        oai = schema.to_openai_format()
        assert oai["type"] == "function"
        assert oai["function"]["name"] == "bash"
        assert oai["function"]["description"] == "Run a command"

    def test_schema_is_frozen(self):
        schema = ToolSchema(name="x", description="x", parameters={})
        with pytest.raises(AttributeError):
            schema.name = "y"
