"""Executable module for ``python -m coding_agent``.

The Click command implementation lives in :mod:`coding_agent.cli.main`.  When
this module is imported as ``coding_agent.__main__``, expose that implementation
module directly so existing tests and callers that patch attributes on
``coding_agent.__main__`` still patch the command callbacks' globals.
"""

from __future__ import annotations

import sys

from coding_agent.cli import main as _cli_main

if __name__ == "__main__":
    _cli_main.main()
else:
    sys.modules[__name__] = _cli_main
