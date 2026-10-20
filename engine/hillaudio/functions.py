"""Automated functions offered by the New Project wizard (SPEC §6.1), and their
prerequisites. The UI reads these definitions from the API rather than keeping
its own copy, so the dependency rules live in one place.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Function:
    key: str
    label: str
    requires: tuple[str, ...] = ()
    needs_text: bool = False  # needs an imported manuscript to run


FUNCTIONS: tuple[Function, ...] = (
    Function("transcribe", "Transcribe"),
    Function("compare_text", "Compare to Text", ("transcribe",), needs_text=True),
    Function("organize", "Organize and Label Files"),
    Function("cleanup", "Clean up stutters / commentary / false starts", ("transcribe",)),
    Function("flubs", "Mark flubbed or incorrect lines", ("compare_text",)),
    Function("silences", "Mark overlong silences"),
    Function("takes", "Compare multiple takes of same line", ("compare_text",)),
)
BY_KEY = {f.key: f for f in FUNCTIONS}
DEFAULT = tuple(f.key for f in FUNCTIONS)  # all on by default (SPEC §6.1)


def with_prerequisites(selected: Iterable[str]) -> list[str]:
    """Close a selection over its prerequisites, in canonical order."""
    chosen: set[str] = set()
    stack = list(selected)
    while stack:
        key = stack.pop()
        if key not in BY_KEY:
            raise ValueError(f"unknown function {key!r}")
        if key not in chosen:
            chosen.add(key)
            stack.extend(BY_KEY[key].requires)
    return [f.key for f in FUNCTIONS if f.key in chosen]
