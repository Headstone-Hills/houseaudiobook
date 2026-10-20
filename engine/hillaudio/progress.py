"""Progress and cancellation callbacks shared by long-running engine work.

Engine functions take these as plain callables so they stay testable without
the job runner; the runner (api/jobs.py) supplies real ones.
"""

from __future__ import annotations

from collections.abc import Callable

Progress = Callable[[float, str], None]  # (fraction 0..1, message)
CheckCancel = Callable[[], None]  # raises Cancelled to abort


class Cancelled(Exception):
    """Raised by a CheckCancel callable when the user cancels the work."""


def no_progress(_fraction: float, _message: str) -> None:
    pass


def never_cancel() -> None:
    pass
