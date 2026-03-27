"""Tests for structured error handling."""

import pytest

from coding_agent.errors import (
    AgentError,
    ErrorHandler,
    APIKeyError,
    ConfigError,
    RepoNotFoundError,
    ProviderError,
    NetworkError,
    RateLimitError,
)


class TestAgentError:
    """Tests for AgentError dataclass."""

    def test_user_error_format(self):
        """Test formatting of user errors."""
        error = AgentError(
            error_type="配置错误",
            message="配置文件格式不正确",
            suggestion="请检查 JSON 语法",
            is_user_error=True,
        )
        
        formatted = error.format_for_display()
        
        assert "❌ 配置错误" in formatted
        assert "配置文件格式不正确" in formatted
        assert "💡 请检查 JSON 语法" in formatted
        assert "📝" not in formatted  # No log path for user errors

    def test_system_error_format(self):
        """Test formatting of system errors."""
        error = AgentError(
            error_type="API 服务错误",
            message="连接超时",
            suggestion="请稍后再试",
            is_user_error=False,
            log_path="~/.coding-agent/logs/error.log",
        )
        
        formatted = error.format_for_display()
        
        assert "❌ API 服务错误" in formatted
        assert "连接超时" in formatted
        assert "📝 详情已记录到:" in formatted
        assert "~/.coding-agent/logs/error.log" in formatted


class TestErrorHandler:
    """Tests for ErrorHandler class."""

    def test_handle_validation_error(self):
        """Test handling of validation errors."""
        exc = ValueError("Invalid configuration")
        
        error = ErrorHandler.handle_exception(exc)
        
        assert error.error_type == "参数错误"
        assert "请检查输入参数是否正确" in error.suggestion
        assert error.is_user_error is True

    def test_handle_file_not_found(self):
        """Test handling of file not found."""
        exc = FileNotFoundError("config.json not found")
        
        error = ErrorHandler.handle_exception(exc)
        
        assert error.error_type == "文件不存在"
        assert error.is_user_error is True

    def test_handle_api_key_error(self):
        """Test handling of API key errors via message detection."""
        exc = Exception("Invalid API key provided")
        
        error = ErrorHandler.handle_exception(exc)
        
        assert error.error_type == "API Key 无效"
        assert error.is_user_error is True
        assert "AGENT_API_KEY" in error.suggestion

    def test_handle_rate_limit(self):
        """Test handling of rate limit errors."""
        exc = Exception("Rate limit exceeded: 429")
        
        error = ErrorHandler.handle_exception(exc)
        
        assert error.error_type == "API 速率限制"
        assert error.is_user_error is False
        assert error.log_path is not None

    def test_handle_network_error(self):
        """Test handling of network errors."""
        exc = Exception("Connection timeout: unable to connect")
        
        error = ErrorHandler.handle_exception(exc)
        
        assert error.error_type == "网络连接错误"
        assert error.is_user_error is False

    def test_handle_unknown_error(self):
        """Test handling of unknown errors."""
        exc = RuntimeError("Something unexpected happened")
        
        error = ErrorHandler.handle_exception(exc)
        
        assert error.error_type == "未知错误"
        assert error.is_user_error is False
        assert error.log_path is not None

    def test_custom_log_path(self):
        """Test using custom log path."""
        exc = Exception("Network error")
        custom_path = "/custom/log/path.log"
        
        error = ErrorHandler.handle_exception(exc, log_path=custom_path)
        
        assert error.log_path == custom_path

    def test_is_user_error_method(self):
        """Test the is_user_error helper method."""
        user_exc = ValueError("User mistake")
        system_exc = Exception("rate limit exceeded")
        
        assert ErrorHandler.is_user_error(user_exc) is True
        assert ErrorHandler.is_user_error(system_exc) is False


class TestCustomExceptions:
    """Tests for custom exception classes."""

    def test_agent_exception_hierarchy(self):
        """Test that custom exceptions work correctly."""
        exceptions = [
            ConfigError("config error"),
            APIKeyError("api key error"),
            RepoNotFoundError("repo error"),
            ProviderError("provider error"),
            NetworkError("network error"),
            RateLimitError("rate limit error"),
        ]
        
        for exc in exceptions:
            assert isinstance(exc, Exception)
            assert str(exc)  # Should have a message

    def test_exception_str(self):
        """Test exception string representation."""
        exc = ConfigError("Invalid JSON")
        
        assert str(exc) == "Invalid JSON"
        assert repr(exc) == "ConfigError('Invalid JSON')"
