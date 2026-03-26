"""Shell command execution."""

from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path

from coding_agent.tools.registry import ToolRegistry


def register_shell_tools(registry: ToolRegistry, cwd: Path | str = ".") -> None:
    """Register shell execution tools.
    
    Args:
        registry: Tool registry to register to
        cwd: Working directory for shell commands
    """
    work_dir = Path(cwd).resolve()

    async def bash(command: str, timeout: int = 60) -> str:
        """Execute a shell command.
        
        Args:
            command: Shell command to execute
            timeout: Maximum execution time in seconds
            
        Returns:
            Command output (stdout + stderr)
        """
        try:
            # Parse command safely using shlex (safer than shell=True)
            # This splits the command into arguments properly
            args = shlex.split(command)
            if not args:
                return json.dumps({
                    "error": "Empty command",
                    "command": command,
                })
            
            # Create subprocess without shell (safer)
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return json.dumps({
                    "error": f"Command timed out after {timeout} seconds",
                    "command": command,
                })
            
            stdout_str = stdout.decode("utf-8", errors="replace")
            stderr_str = stderr.decode("utf-8", errors="replace")
            
            # Combine stdout and stderr
            output = stdout_str
            if stderr_str:
                if output:
                    output += "\n"
                output += f"[stderr]\n{stderr_str}"
            
            # Truncate very long output
            max_output = 10000
            if len(output) > max_output:
                output = output[:max_output] + f"\n... ({len(output) - max_output} more chars)"
            
            return json.dumps({
                "command": command,
                "exit_code": process.returncode,
                "output": output,
            })
            
        except Exception as e:
            return json.dumps({
                "error": str(e),
                "command": command,
            })

    registry.register(
        name="bash",
        description="Execute a shell command in the repository directory.",
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 60)",
                    "default": 60,
                },
            },
            "required": ["command"],
        },
        handler=bash,
    )
