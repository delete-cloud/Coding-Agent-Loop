from __future__ import annotations

from typing import TextIO

from prompt_toolkit import HTML, print_formatted_text
from prompt_toolkit.formatted_text import AnyFormattedText
from prompt_toolkit.output import Output
from prompt_toolkit.output.defaults import create_output

_prompt_output: Output | None = None


def set_prompt_output(output: Output) -> None:
    global _prompt_output
    _prompt_output = output


def get_prompt_output(file: TextIO | None = None) -> Output:
    if _prompt_output is not None:
        return _prompt_output
    return create_output(stdout=file)


def print_pt(
    message: AnyFormattedText | str = "",
    *,
    file: TextIO | None = None,
    output: Output | None = None,
    end: str = "\n",
) -> None:
    print_formatted_text(
        message,
        file=file,
        output=output or get_prompt_output(file),
        end=end,
    )


def print_html(
    html: str,
    *,
    file: TextIO | None = None,
    output: Output | None = None,
    end: str = "\n",
) -> None:
    print_pt(HTML(html), file=file, output=output, end=end)
