import pytest
from agentkit.instruction.normalize import normalize_instruction


class TestNormalizeInstruction:
    def test_string_becomes_user_message(self):
        result = normalize_instruction("hello")
        assert result == {"role": "user", "content": "hello"}

    def test_dict_with_role_passes_through(self):
        msg = {"role": "system", "content": "you are helpful"}
        result = normalize_instruction(msg)
        assert result == msg

    def test_dict_without_role_gets_user(self):
        msg = {"content": "do the thing"}
        result = normalize_instruction(msg)
        assert result == {"role": "user", "content": "do the thing"}

    def test_list_of_strings(self):
        result = normalize_instruction(["hello", "world"])
        assert len(result) == 2
        assert result[0] == {"role": "user", "content": "hello"}
        assert result[1] == {"role": "user", "content": "world"}

    def test_list_of_dicts(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"content": "usr"},
        ]
        result = normalize_instruction(msgs)
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"

    def test_mixed_list(self):
        result = normalize_instruction(["text", {"role": "system", "content": "sys"}])
        assert result[0] == {"role": "user", "content": "text"}
        assert result[1] == {"role": "system", "content": "sys"}

    def test_none_raises(self):
        with pytest.raises(TypeError, match="cannot normalize"):
            normalize_instruction(None)

    def test_empty_string(self):
        result = normalize_instruction("")
        assert result == {"role": "user", "content": ""}

    def test_empty_list(self):
        result = normalize_instruction([])
        assert result == []
