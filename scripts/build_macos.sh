#!/usr/bin/env bash
# Build HillAudiobook.app (and a .dmg) on macOS. See docs/PACKAGING.md.
#
#   scripts/build_macos.sh
#
# Signing is optional ("signed-ready"): set APPLE_SIGNING_IDENTITY (a Developer ID
# Application identity in the keychain) to sign with the hardened runtime, and
# APPLE_ID + APPLE_PASSWORD (app-specific) + APPLE_TEAM_ID, or APPLE_API_KEY +
# APPLE_API_ISSUER + APPLE_API_KEY_PATH, to notarize as well. Without them the
# app is ad-hoc signed: it runs on the build machine and for testing.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[[ "$(uname)" == "Darwin" ]] || { echo "error: build the .app on macOS"; exit 1; }
ARCH="$(uname -m)"  # arm64 (Apple Silicon) or x86_64 (Intel)

# 1. ffmpeg + ffprobe to bundle: FFMPEG_DIR, or static builds downloaded here.
if [[ -z "${FFMPEG_DIR:-}" ]]; then
  FFMPEG_DIR="$ROOT/build/ffmpeg-$ARCH"
  if [[ ! -x "$FFMPEG_DIR/ffmpeg" || ! -x "$FFMPEG_DIR/ffprobe" ]]; then
    mkdir -p "$FFMPEG_DIR"
    for tool in ffmpeg ffprobe; do
      url="https://ffmpeg.martin-riedl.de/redirect/latest/macos/$ARCH/release/$tool.zip"
      echo "downloading $tool ($url)"
      curl -fsSL "$url" -o "$FFMPEG_DIR/$tool.zip"
      (cd "$FFMPEG_DIR" && unzip -oq "$tool.zip" && rm "$tool.zip" && chmod +x "$tool")
      shasum -a 256 "$FFMPEG_DIR/$tool"
    done
  fi
fi
"$FFMPEG_DIR/ffmpeg" -hide_banner -encoders 2>/dev/null | grep -q libmp3lame \
  || { echo "error: $FFMPEG_DIR/ffmpeg has no libmp3lame (MP3 export needs it)"; exit 1; }
export FFMPEG_DIR

# 2. The engine (PyInstaller onedir) with the built UI and ffmpeg inside.
scripts/build_engine.sh

# 3. Sign every executable file in the engine folder (codesign can't sign a
#    folder's contents for us when it is a plain resource of the app).
IDENTITY="${APPLE_SIGNING_IDENTITY:--}"
ENT="$ROOT/shell/src-tauri/entitlements.plist"
while IFS= read -r -d '' f; do
  if file -b "$f" | grep -q "Mach-O"; then
    if [[ "$IDENTITY" == "-" ]]; then
      codesign --force --sign - "$f"
    else
      codesign --force --timestamp --options runtime --entitlements "$ENT" --sign "$IDENTITY" "$f"
    fi
  fi
done < <(find build/engine/hillaudio-engine -type f -print0)

# 4. The app (Tauri signs and, with credentials, notarizes it).
command -v cargo-tauri >/dev/null || cargo install tauri-cli --version "^2" --locked
(cd shell && cargo tauri build --bundles app,dmg)
APP="shell/target/release/bundle/macos/HillAudiobook.app"

# 5. Smoke test: the app starts its own engine and the engine answers.
"$APP/Contents/MacOS/hillaudiobook" --smoke-test
if [[ "$IDENTITY" != "-" ]]; then
  codesign --verify --deep --strict --verbose=2 "$APP"
  spctl --assess --type execute --verbose "$APP" || echo "note: Gatekeeper assessment needs notarization"
fi
# 6. A real first run of the engine inside the app: model download from Hugging
#    Face, transcription, MP3 export with the bundled ffmpeg, the guide PDF.
if [[ "${SMOKE_FIRST_RUN:-1}" == "1" ]]; then
  engine/.venv/bin/python scripts/smoke_engine.py \
    "$APP/Contents/Resources/engine/hillaudio-engine" --model tiny.en --bundled-ffmpeg
fi
echo "built: $APP"
ls shell/target/release/bundle/dmg/*.dmg 2>/dev/null || true
