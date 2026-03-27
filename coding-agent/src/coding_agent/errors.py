"""Structured error handling for Coding Agent.

This module provides structured error handling with user-friendly messages,
automatic error classification, and proper logging integration.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class AgentError:
    """Structured error information.
    
    Attributes:
        error_type: Human-readable error type name
        message: Detailed error message
        suggestion: Actionable suggestion for the user
        log_path: Path to detailed logs (for system errors)
        is_user_error: Whether this is a user error (vs system error)
    """
    error_type: str
    message: str
    suggestion: str
    log_path: Optional[str] = None
    is_user_error: bool = True

    def format_for_display(self) -> str:
        """Format error for user display.
        
        Returns:
            Formatted error message with appropriate styling.
        """
        if self.is_user_error:
            return f"""❌ {self.error_type}
   {self.message}
   
   💡 {self.suggestion}"""
        else:
            return f"""❌ {self.error_type}
   {self.message}
   
   📝 详情已记录到: {self.log_path}"""


class ErrorHandler:
    """Handle and format errors with automatic classification."""

    # Error mapping: exception type -> (title, suggestion, is_user_error)
    _ERROR_MAP: dict[str, tuple[str, str, bool]] = {
        # User errors
        "ValidationError": (
            "配置错误",
            "请检查配置文件格式是否正确",
            True,
        ),
        "PydanticValidationError": (
            "配置错误",
            "请检查配置文件格式",
            True,
        ),
        "AuthenticationError": (
            "API Key 无效",
            "请设置 AGENT_API_KEY 环境变量或使用 --api-key 参数",
            True,
        ),
        "FileNotFoundError": (
            "文件不存在",
            "请检查文件路径是否正确",
            True,
        ),
        "RepoNotFoundError": (
            "仓库路径不存在",
            "请检查 --repo 参数指定的路径",
            True,
        ),
        "ValueError": (
            "参数错误",
            "请检查输入参数是否正确",
            True,
        ),
        # System errors
        "RateLimitError": (
            "API 速率限制",
            "请稍后再试，或升级 API 套餐",
            False,
        ),
        "ProviderError": (
            "API 服务错误",
            "LLM 服务暂时不可用，请稍后再试",
            False,
        ),
        "NetworkError": (
            "网络连接错误",
            "请检查网络连接后重试",
            False,
        ),
        "TimeoutError": (
            "请求超时",
            "请求处理时间过长，请稍后再试",
            False,
        ),
    }

    _DEFAULT_LOG_PATH = str(Path.home() / ".coding-agent" / "logs" / "error.log")

    @classmethod
    def handle_exception(
        cls,
        exc: Exception,
        log_path: Optional[str] = None,
    ) -> AgentError:
        """Convert exception to structured error.
        
        Args:
            exc: The exception to handle
            log_path: Optional custom log path for system errors
            
        Returns:
            Structured AgentError with user-friendly information
        """
        log_path = log_path or cls._DEFAULT_LOG_PATH
        exc_type = type(exc).__name__
        exc_message = str(exc)

        # Look up error in mapping
        if exc_type in cls._ERROR_MAP:
            title, suggestion, is_user = cls._ERROR_MAP[exc_type]
            return AgentError(
                error_type=title,
                message=exc_message,
                suggestion=suggestion,
                is_user_error=is_user,
                log_path=None if is_user else log_path,
            )

        # Check for common patterns in error messages
        error_lower = exc_message.lower()
        
        # API key related errors
        if any(kw in error_lower for kw in ["api key", "apikey", "authentication", "auth", "unauthorized"]):
            return AgentError(
                error_type="API Key 无效",
                message=exc_message,
                suggestion="请设置 AGENT_API_KEY 环境变量或使用 --api-key 参数",
                is_user_error=True,
            )
        
        # Rate limit errors
        if any(kw in error_lower for kw in ["rate limit", "too many requests", "429"]):
            return AgentError(
                error_type="API 速率限制",
                message=exc_message,
                suggestion="请稍后再试，或升级 API 套餐",
                is_user_error=False,
                log_path=log_path,
            )
        
        # Network errors
        if any(kw in error_lower for kw in ["connection", "network", "timeout", "unable to connect"]):
            return AgentError(
                error_type="网络连接错误",
                message=exc_message,
                suggestion="请检查网络连接后重试",
                is_user_error=False,
                log_path=log_path,
            )
        
        # File/path errors
        if any(kw in error_lower for kw in ["no such file", "not found", "does not exist"]):
            return AgentError(
                error_type="文件不存在",
                message=exc_message,
                suggestion="请检查文件路径是否正确",
                is_user_error=True,
            )

        # Unknown error - treat as system error
        return AgentError(
            error_type="未知错误",
            message=exc_message,
            suggestion="请查看日志或提交 issue",
            is_user_error=False,
            log_path=log_path,
        )

    @classmethod
    def is_user_error(cls, exc: Exception) -> bool:
        """Check if an exception is a user error.
        
        Args:
            exc: The exception to check
            
        Returns:
            True if this is a user error, False if system error
        """
        error = cls.handle_exception(exc)
        return error.is_user_error


# Convenience exceptions for common error cases

class AgentException(Exception):
    """Base exception for agent-specific errors."""
    pass


class ConfigError(AgentException):
    """Raised when there's a configuration error."""
    pass


class APIKeyError(AgentException):
    """Raised when API key is invalid or missing."""
    pass


class RepoNotFoundError(AgentException):
    """Raised when repository path doesn't exist."""
    pass


class ProviderError(AgentException):
    """Raised when LLM provider encounters an error."""
    pass


class NetworkError(AgentException):
    """Raised when network operation fails."""
    pass


class RateLimitError(AgentException):
    """Raised when API rate limit is hit."""
    pass
