"""Tests for ErrorHandler."""

import re
import pytest
from coding_agent.errors import AgentError, ErrorHandler


class TestErrorHandler:
    """Test suite for ErrorHandler."""
    
    def test_exception_type_mapping(self):
        """Test exact exception type matching."""
        test_cases = [
            (TimeoutError("Connection timed out"), "连接超时", False),
            (ConnectionError("Network unreachable"), "网络连接错误", False),
            (FileNotFoundError("file.txt not found"), "文件不存在", True),
            (PermissionError("Access denied"), "权限不足", True),
            (ValueError("Invalid input"), "参数错误", True),
            (KeyError("missing_key"), "键不存在", True),
            (IndexError("list index out of range"), "索引越界", True),
            (TypeError("int + str"), "类型错误", True),
        ]
        
        for exc, expected_type, is_user_error in test_cases:
            error = ErrorHandler.handle_exception(exc)
            assert error.error_type == expected_type
            assert error.is_user_error == is_user_error
    
    def test_regex_pattern_matching(self):
        """Test regex pattern matching on error messages."""
        test_cases = [
            ("Invalid API key provided", "API Key 无效"),
            ("Rate limit exceeded, please wait", "API 速率限制"),
            ("Your quota has been exceeded", "API 配额已用尽"),
            ("Connection timeout to server", "网络连接错误"),
            ("Server error 500 occurred", "API 服务错误"),
            ("Unauthorized access to resource", "认证失败"),
            ("Resource not found 404", "资源不存在"),
            ("Bad request: invalid parameter", "请求格式错误"),
            ("Request timed out after 30s", "请求超时"),
        ]
        
        for message, expected_type in test_cases:
            exc = Exception(message)
            error = ErrorHandler.handle_exception(exc)
            assert error.error_type == expected_type, f"Failed for message: {message}"
    
    def test_unknown_error(self):
        """Test handling of unknown error types."""
        exc = Exception("Some random unknown error")
        error = ErrorHandler.handle_exception(exc)
        
        assert error.error_type == "未知错误"
        assert error.suggestion == "请查看日志或提交 issue"
        assert error.is_user_error is False
        assert error.log_path is not None
    
    def test_user_error_no_log_path(self):
        """Test that user errors don't have a log path."""
        exc = ValueError("Invalid input")
        error = ErrorHandler.handle_exception(exc)
        
        assert error.is_user_error is True
        assert error.log_path is None
    
    def test_system_error_has_log_path(self):
        """Test that system errors have a log path."""
        exc = ConnectionError("Network error")
        error = ErrorHandler.handle_exception(exc)
        
        assert error.is_user_error is False
        assert error.log_path is not None
    
    def test_add_custom_pattern(self):
        """Test adding custom error patterns."""
        ErrorHandler.add_pattern(
            r"custom_error_code_\d+",
            "自定义错误",
            "请联系支持团队",
            False,
        )
        
        exc = Exception("Encountered custom_error_code_123")
        error = ErrorHandler.handle_exception(exc)
        
        assert error.error_type == "自定义错误"
        assert error.suggestion == "请联系支持团队"
    
    def test_add_custom_exception_type(self):
        """Test adding custom exception type mapping."""
        ErrorHandler.add_exception_type(
            "CustomException",
            "自定义异常",
            "这是自定义异常",
            True,
        )
        
        class CustomException(Exception):
            pass
        
        exc = CustomException("Test")
        error = ErrorHandler.handle_exception(exc)
        
        assert error.error_type == "自定义异常"
    
    def test_exception_type_priority_over_regex(self):
        """Test that exception type matching takes priority over regex."""
        # This message would match the "timeout" regex
        exc = TimeoutError("This is a custom timeout message")
        error = ErrorHandler.handle_exception(exc)
        
        # Should match the exception type, not the regex
        assert error.error_type == "连接超时"
    
    def test_case_insensitive_regex(self):
        """Test that regex patterns are case insensitive."""
        test_cases = [
            "INVALID API KEY",
            "Invalid Api Key",
            "invalid api key",
        ]
        
        for message in test_cases:
            exc = Exception(message)
            error = ErrorHandler.handle_exception(exc)
            assert error.error_type == "API Key 无效"


class TestAgentError:
    """Test suite for AgentError dataclass."""
    
    def test_agent_error_creation(self):
        """Test creating an AgentError."""
        error = AgentError(
            error_type="测试错误",
            message="Something went wrong",
            suggestion="Try again",
            is_user_error=True,
            log_path=None,
        )
        
        assert error.error_type == "测试错误"
        assert error.message == "Something went wrong"
        assert error.suggestion == "Try again"
        assert error.is_user_error is True
        assert error.log_path is None
