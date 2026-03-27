"""UI package for Coding Agent."""

from coding_agent.ui.headless import HeadlessConsumer
from coding_agent.ui.http_server import app, wait_for_approval
from coding_agent.ui.rich_consumer import RichConsumer
from coding_agent.ui.rich_tui import CodingAgentTUI

__all__ = [
    "HeadlessConsumer",
    "RichConsumer",
    "CodingAgentTUI",
    "app",
    "wait_for_approval",
]
