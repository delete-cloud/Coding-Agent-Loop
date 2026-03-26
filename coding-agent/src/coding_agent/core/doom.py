"""Doom loop detection for repetitive tool calls."""

import hashlib
import json


class DoomDetector:
    """Detect repetitive tool calls that indicate the agent is stuck."""

    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self.last_tool: str | None = None
        self.last_args_hash: str | None = None
        self.count: int = 0

    def observe(self, tool: str, args: dict) -> bool:
        """Returns True if doom loop detected."""
        args_hash = hashlib.md5(
            json.dumps(args, sort_keys=True).encode()
        ).hexdigest()
        
        if tool == self.last_tool and args_hash == self.last_args_hash:
            self.count += 1
        else:
            self.last_tool = tool
            self.last_args_hash = args_hash
            self.count = 1
        
        return self.count >= self.threshold
