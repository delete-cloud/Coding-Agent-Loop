"""Instruction normalizer — flexible input, uniform output."""

from __future__ import annotations

from typing import Any, overload


@overload
def normalize_instruction(instruction: str) -> dict[str, Any]: ...
@overload
def normalize_instruction(instruction: dict[str, Any]) -> dict[str, Any]: ...
@overload
def normalize_instruction(instruction: list[Any]) -> list[dict[str, Any]]: ...


def normalize_instruction(
    instruction: str | dict[str, Any] | list[Any],
) -> dict[str, Any] | list[dict[str, Any]]:
    """Normalize an instruction to standard message format.

    Args:
        instruction: A string, dict, or list of strings/dicts.

    Returns:
        A message dict or list of message dicts.

    Raises:
        TypeError: If instruction type is not supported.
    """
    if isinstance(instruction, str):
        return {"role": "user", "content": instruction}
    elif isinstance(instruction, dict):
        if "role" not in instruction:
            return {"role": "user", **instruction}
        return instruction
    elif isinstance(instruction, list):
        return [normalize_instruction(item) for item in instruction]
    else:
        raise TypeError(
            f"cannot normalize instruction of type {type(instruction).__name__}"
        )
