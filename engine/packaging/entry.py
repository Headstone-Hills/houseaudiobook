"""The frozen engine's entry point: `hillaudio-engine serve ...` (D-054)."""

import multiprocessing

from hillaudio.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()  # a frozen program must handle child re-entry
    raise SystemExit(main())
