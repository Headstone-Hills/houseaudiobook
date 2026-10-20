"""Per-user app settings: the recent-projects list and the default project location.

Stored in `$HILLAUDIO_HOME` (default `~/.hillaudiobook`). Only paths and names
are kept here, never audio or text.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from hillaudio.store.project import DB_NAME, now_iso

MAX_RECENT = 20


def app_home() -> Path:
    return Path(os.environ.get("HILLAUDIO_HOME", "~/.hillaudiobook")).expanduser()


def default_location() -> Path:
    return Path("~/HillAudiobook Projects").expanduser()


@dataclass(frozen=True)
class Recent:
    path: str
    name: str
    opened_at: str


def _recent_file() -> Path:
    return app_home() / "recent.json"


def load_recent() -> list[Recent]:
    try:
        raw = json.loads(_recent_file().read_text(encoding="utf-8"))
        items = [Recent(**r) for r in raw]
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return []
    # Hide projects that were moved or deleted since; keep the file as-is.
    return [r for r in items if (Path(r.path) / DB_NAME).is_file()]


def remember(path: Path, name: str) -> None:
    items = [r for r in load_recent() if r.path != str(path)]
    items.insert(0, Recent(str(path), name, now_iso()))
    f = _recent_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(r) for r in items[:MAX_RECENT]], indent=2), encoding="utf-8")
    tmp.replace(f)
