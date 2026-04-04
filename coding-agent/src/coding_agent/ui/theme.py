"""Centralized theme configuration for Coding Agent TUI."""


class Theme:
    """TUI theme with colors, icons, and layout constants."""

    # ==================== COLORS ====================
    class Colors:
        PRIMARY = "cyan"
        SUCCESS = "bold green"
        ERROR = "bold red"
        WARNING = "bold yellow"
        INFO = "blue"
        TEXT_PRIMARY = "white"
        TEXT_MUTED = "dim white"
        BORDER_DEFAULT = "cyan"
        BORDER_ACTIVE = "bold cyan"
        USER_MSG = "green"
        ASSISTANT_MSG = "blue"
        SYSTEM_MSG = "yellow"
        SEPARATOR = "dim"
        USER_PANEL_BORDER = "green"
        ASSISTANT_PANEL_BORDER = "blue"

    # ==================== ICONS ====================
    class Icons:
        AGENT = "🤖"
        USER = "👤"
        TOOL = "🔧"
        PLAN = "📋"
        SUCCESS = "✅"
        ERROR = "❌"
        WARNING = "⚠️"
        THINKING = "💭"
        FILE = "📄"
        SEARCH = "🔍"
        BASH = "⚡"

    # ==================== LAYOUT ====================
    class Layout:
        HEADER_HEIGHT = 3
        TOOL_PANEL_HEIGHT = 12
        PANEL_PADDING = (0, 1)
        BOX_STYLE = "ROUNDED"
        SEPARATOR_CHAR = "─"

    # ==================== TEXT STYLES ====================
    class Styles:
        TITLE = "bold cyan"
        HEADER = "bold white"
        SUBTITLE = "dim cyan"
        CODE = "dim"


# Global theme instance
theme = Theme()
