#!/usr/bin/env bash
# Build the standalone engine (PyInstaller onedir) into build/engine/hillaudio-engine/.
# Works on macOS and Linux. FFMPEG_DIR (a folder with ffmpeg and ffprobe) bundles
# those tools; without it the engine uses ffmpeg from PATH. See docs/PACKAGING.md.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# The bundled ffmpeg also satisfies `make setup` on a machine without one.
[[ -n "${FFMPEG_DIR:-}" ]] && export PATH="$FFMPEG_DIR:$PATH"
make setup >/dev/null
engine/.venv/bin/pip install -q "pyinstaller==6.22.3"
(cd web && npm run build --silent)
rm -rf build/engine build/pyinstaller
engine/.venv/bin/pyinstaller engine/packaging/hillaudio-engine.spec \
  --distpath build/engine --workpath build/pyinstaller --noconfirm --log-level WARN
echo "engine: build/engine/hillaudio-engine/hillaudio-engine"
