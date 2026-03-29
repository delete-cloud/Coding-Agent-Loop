# 任务 2.4: 结构化错误提示 - 详细实现方案

## 需求规格

### 功能需求
1. 区分用户错误 vs 系统错误
2. 用户错误：友好提示 + 解决建议
3. 系统错误：简略信息 + 日志路径

### 错误分类

```python
USER_ERRORS = {
    "ConfigError": "配置错误",
    "APIKeyError": "API Key 无效",
    "RepoNotFound": "仓库路径不存在",
}

SYSTEM_ERRORS = {
    "ProviderError": "API 服务错误",
    "NetworkError": "网络连接错误",
    "DiskFull": "磁盘空间不足",
}
```

---

## 实现方案

```python
# src/coding_agent/errors.py
"""Structured error handling."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class AgentError:
    """Structured error information."""
    
    error_type: str
    message: str
    suggestion: str
    log_path: Optional[str] = None
    is_user_error: bool = True
    
    def format_for_display(self) -> str:
        """Format error for user display."""
        if self.is_user_error:
            return f"""❌ {self.error_type}
   {self.message}
   
   💡 {self.suggestion}"""
        else:
            return f"""❌ {self.error_type}
   {self.message}
   
   📝 详情已记录到: {self.log_path}"""


class ErrorHandler:
    """Handle and format errors."""
    
    @staticmethod
    def handle_exception(exc: Exception) -> AgentError:
        """Convert exception to structured error."""
        error_map = {
            "PydanticValidationError": (
                "配置错误",
                "请检查配置文件格式",
                True,
            ),
            "AuthenticationError": (
                "API Key 无效",
                "请设置 AGENT_API_KEY 环境变量或使用 --api-key",
                True,
            ),
            "RateLimitError": (
                "API 速率限制",
                "请稍后再试，或升级 API 套餐",
                False,
            ),
        }
        
        exc_type = type(exc).__name__
        if exc_type in error_map:
            title, suggestion, is_user = error_map[exc_type]
            return AgentError(
                error_type=title,
                message=str(exc),
                suggestion=suggestion,
                is_user_error=is_user,
                log_path="~/.coding-agent/logs/error.log" if not is_user else None,
            )
        
        # Unknown error
        return AgentError(
            error_type="未知错误",
            message=str(exc),
            suggestion="请查看日志或提交 issue",
            is_user_error=False,
            log_path="~/.coding-agent/logs/error.log",
        )
```

---

## 集成到 Loop

```python
# src/coding_agent/core/loop.py

from coding_agent.errors import ErrorHandler

class AgentLoop:
    async def run_turn(self, user_input: str) -> TurnOutcome:
        try:
            # ... existing logic ...
            pass
        except Exception as e:
            error = ErrorHandler.handle_exception(e)
            
            # Log full traceback
            logger.exception("Agent error")
            
            # Display user-friendly message
            await self.consumer.emit(ErrorMessage(
                content=error.format_for_display(),
            ))
            
            return TurnOutcome(
                stop_reason="error",
                error=error,
            )
```

---

## 预估工作量

| 任务 | 时间 |
|------|------|
| 错误分类和格式化 | 30 min |
| 集成到 Loop | 20 min |
| 测试 | 20 min |
| **总计** | **~70 min** |
