"""UI package for Coding Agent."""

from importlib import import_module
from typing import cast

from coding_agent.ui.headless import HeadlessConsumer
from coding_agent.ui.rich_consumer import RichConsumer

__all__ = [
    "HeadlessConsumer",
    "RichConsumer",
]


def __getattr__(name: str) -> object:
    if name == "app":
        http_server = import_module("coding_agent.server.http_server")
        return cast(object, http_server.app)
    if name == "wait_for_approval":
        http_server = import_module("coding_agent.server.http_server")
        return cast(object, http_server.wait_for_approval)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
