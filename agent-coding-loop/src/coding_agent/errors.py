"""Error handling with precise pattern matching."""

import re
from dataclasses import dataclass
from typing import Pattern


@dataclass
class AgentError:
    """Structured error information for agent operations.
    
    Attributes:
        error_type: Human-readable error type/category.
        message: Original error message.
        suggestion: Suggested action to resolve the error.
        is_user_error: Whether this is a user-fixable error.
        log_path: Path to relevant log file for non-user errors.
    """
    error_type: str
    message: str
    suggestion: str
    is_user_error: bool
    log_path: str | None = None


class ErrorHandler:
    """Handle and format errors with precise matching.
    
    This class provides intelligent error classification by matching
    exception types and messages against known patterns. It uses both
    exact exception type matching and regex pattern matching to provide
    accurate error categorization and helpful suggestions.
    
    Example:
        ```python
        try:
            api.call()
        except Exception as e:
            error = ErrorHandler.handle_exception(e)
            print(f"Error: {error.error_type}")
            print(f"Suggestion: {error.suggestion}")
        ```
    """
    
    # 更精确的匹配规则：(正则, 错误类型, 建议, 是否用户错误)
    _ERROR_PATTERNS: list[tuple[Pattern, str, str, bool]] = [
        # API Key 错误
        (
            re.compile(r'\b(?:invalid|bad|wrong)\s+(?:api\s*key|apikey|token|auth)\b', re.I),
            "API Key 无效",
            "请检查 AGENT_API_KEY 环境变量或使用 --api-key 参数",
            True,
        ),
        # 速率限制
        (
            re.compile(r'\brate\s*limit|too\s+many\s+requests|429\b', re.I),
            "API 速率限制",
            "请稍后再试，或升级 API 套餐",
            True,
        ),
        # 配额用尽
        (
            re.compile(r'\bquota(?:\s*exceeded|.+exceeded)|billing|payment\b', re.I),
            "API 配额已用尽",
            "请检查 API 账户余额或账单设置",
            True,
        ),
        # 网络错误
        (
            re.compile(r'\bconnection|timeout|network|dns|unreachable\b', re.I),
            "网络连接错误",
            "请检查网络连接，或稍后重试",
            False,
        ),
        # 服务器错误
        (
            re.compile(r'\bserver\s*error|5\d{2}\b', re.I),
            "API 服务错误",
            "API 服务暂时不可用，请稍后重试",
            False,
        ),
        # 认证失败
        (
            re.compile(r'\bunauthorized|401|auth\s*fail|forbidden|403\b', re.I),
            "认证失败",
            "请检查您的认证凭据是否有权限访问此资源",
            True,
        ),
        # 资源不存在
        (
            re.compile(r'\bnot\s*found|404|does\s*not\s*exist|missing\b', re.I),
            "资源不存在",
            "请检查请求的资源路径或ID是否正确",
            True,
        ),
        # 请求格式错误
        (
            re.compile(r'\bbad\s*request|400|invalid\s*(?:param|argument|input)|malformed\b', re.I),
            "请求格式错误",
            "请检查请求参数是否正确",
            True,
        ),
        # 超时错误
        (
            re.compile(r'\btimed?\s*out|deadline\s*exceeded\b', re.I),
            "请求超时",
            "请求处理时间过长，请稍后重试或简化请求",
            False,
        ),
    ]
    
    # 异常类型映射（优先于正则匹配）
    _EXCEPTION_TYPES: dict[str, tuple[str, str, bool]] = {
        "AuthenticationError": ("API Key 无效", "请检查 AGENT_API_KEY 环境变量", True),
        "RateLimitError": ("API 速率限制", "请稍后再试", True),
        "TimeoutError": ("连接超时", "请检查网络连接", False),
        "ConnectionError": ("网络连接错误", "请检查网络连接", False),
        "FileNotFoundError": ("文件不存在", "请检查文件路径是否正确", True),
        "PermissionError": ("权限不足", "请检查文件或目录权限", True),
        "ValueError": ("参数错误", "请检查输入参数", True),
        "KeyError": ("键不存在", "请检查字典键名是否正确", True),
        "IndexError": ("索引越界", "请检查索引范围", True),
        "TypeError": ("类型错误", "请检查参数类型", True),
        "RuntimeError": ("运行时错误", "执行过程中发生错误", False),
    }
    
    _DEFAULT_LOG_PATH: str = "~/.coding-agent/logs/error.log"
    
    @classmethod
    def handle_exception(cls, exc: Exception) -> AgentError:
        """Convert exception to structured error with precise matching.
        
        This method first attempts to match the exception by its type name
        for exact classification. If no type match is found, it falls back
        to regex pattern matching on the error message.
        
        Args:
            exc: The exception to handle.
        
        Returns:
            An AgentError with detailed error information and suggestions.
        """
        exc_type = type(exc).__name__
        exc_module = type(exc).__module__
        message = str(exc)
        
        # 1. 优先匹配异常类型（精确匹配）
        full_name = f"{exc_module}.{exc_type}"
        if exc_type in cls._EXCEPTION_TYPES:
            title, suggestion, is_user = cls._EXCEPTION_TYPES[exc_type]
            return AgentError(
                error_type=title,
                message=message,
                suggestion=suggestion,
                is_user_error=is_user,
                log_path=None if is_user else cls._DEFAULT_LOG_PATH,
            )
        
        # 2. 正则表达式匹配
        for pattern, title, suggestion, is_user in cls._ERROR_PATTERNS:
            if pattern.search(message):
                return AgentError(
                    error_type=title,
                    message=message,
                    suggestion=suggestion,
                    is_user_error=is_user,
                    log_path=None if is_user else cls._DEFAULT_LOG_PATH,
                )
        
        # 3. 默认：未知错误
        return AgentError(
            error_type="未知错误",
            message=message,
            suggestion="请查看日志或提交 issue",
            is_user_error=False,
            log_path=cls._DEFAULT_LOG_PATH,
        )
    
    @classmethod
    def add_pattern(
        cls,
        pattern: str | Pattern,
        error_type: str,
        suggestion: str,
        is_user_error: bool,
    ) -> None:
        """Add a custom error pattern.
        
        Args:
            pattern: Regex pattern string or compiled Pattern.
            error_type: Human-readable error type.
            suggestion: Suggested action.
            is_user_error: Whether this is a user-fixable error.
        """
        if isinstance(pattern, str):
            pattern = re.compile(pattern, re.I)
        cls._ERROR_PATTERNS.append((pattern, error_type, suggestion, is_user_error))
    
    @classmethod
    def add_exception_type(
        cls,
        exc_type: str,
        error_type: str,
        suggestion: str,
        is_user_error: bool,
    ) -> None:
        """Add a custom exception type mapping.
        
        Args:
            exc_type: Exception class name.
            error_type: Human-readable error type.
            suggestion: Suggested action.
            is_user_error: Whether this is a user-fixable error.
        """
        cls._EXCEPTION_TYPES[exc_type] = (error_type, suggestion, is_user_error)
