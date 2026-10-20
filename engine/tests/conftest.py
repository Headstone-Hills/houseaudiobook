from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _hashes(root: Path) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.iterdir())
        if p.is_file()
    }


@pytest.fixture(scope="session", autouse=True)
def fixtures_untouched() -> Iterator[None]:
    """Fail the run if any test modified committed fixture audio or text.

    Fixtures stand in for user source files, which must never be modified.
    """
    before = _hashes(FIXTURES)
    yield
    after = _hashes(FIXTURES)
    assert after == before, "a test modified files in engine/fixtures"
