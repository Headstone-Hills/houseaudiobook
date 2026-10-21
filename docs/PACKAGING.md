# Packaging HillAudiobook for macOS (M9)

The Mac app is a small Tauri 2 window (`shell/`) around the same engine and web
UI you get with `make serve`. Nothing about the app is Mac-only except the
wrapper: the engine is a PyInstaller build of `engine/`, and the window loads
the engine's own loopback page.

```
HillAudiobook.app/Contents/
  MacOS/hillaudiobook          Tauri shell (Rust): splash, starts the engine, opens its URL
  Resources/engine/
    hillaudio-engine           the engine: `serve --port 0 --exit-with-stdin`
    _internal/web/             the built web UI (web/dist)
    _internal/bin/ffmpeg, ffprobe   bundled tools (the engine uses these, D-056)
```

## How the pieces talk
1. The shell starts `Resources/engine/hillaudio-engine serve --port 0 --exit-with-stdin`.
2. The engine binds a free loopback port and prints
   `HILLAUDIO_READY port=N url=http://127.0.0.1:N/` (`hillaudio/cli.py`).
3. The shell (`shell/launcher`) reads that line. It accepts only a loopback URL
   and gives up after 90 s, or as soon as the engine exits. Then it points the
   window at the URL.
4. On quit the shell closes the engine's stdin and waits up to 5 s, then kills
   it. If the shell is killed instead, the engine sees stdin close and exits
   by itself, so no orphan servers are left behind.

Speech models are not bundled. On first run the app shows what it needs (model,
size, source) and downloads only when asked: from a pinned Hugging Face
revision, checksum-verified, resumable (D-055).

## Build

On a Mac with Xcode command-line tools, Python 3.11, Node 22 and Rust (stable):

```
scripts/build_macos.sh
```

It does six things:
1. **Gets ffmpeg and ffprobe.**
   - Uses `FFMPEG_DIR` if set (a folder with both tools). Otherwise `scripts/build_ffmpeg.sh` builds them into `build/ffmpeg-<arch>/` (about 3 min; CI caches it). That build is:
     - static, **LGPL-only** (no `--enable-gpl`, no nonfree, `--disable-autodetect`), with libmp3lame and nothing else external;
     - made from LAME 3.100, checked against a pinned SHA-256;
     - made from the FFmpeg release tarball, checked against its signature from the pinned FFmpeg release key `FCF986EA15E6E293A5644F10B4322F04D67658D8`;
     - shipped with its license texts and `-buildconf`.
   - Either way, libmp3lame is required (MP3 export).
2. **Builds the engine** with `scripts/build_engine.sh`: the web build, then PyInstaller onedir into `build/engine/hillaudio-engine/`.
3. **Signs every Mach-O file** in the engine folder. It uses the identity if one is given, otherwise ad-hoc.
4. **Runs `cargo tauri build --bundles app,dmg`** in `shell/`.
5. **Runs `HillAudiobook.app/Contents/MacOS/hillaudiobook --smoke-test`.** The app starts its own engine and checks `/api/health` and the UI page.
6. **Runs `scripts/smoke_engine.py --bundled-ffmpeg`** on the engine inside the app: a real first run.
   - It downloads tiny.en from Hugging Face, imports the fixtures, transcribes, exports MP3 and makes the guide PDF.
   - It requires the bundled ffmpeg to be the one in use.
   - Skip it with `SMOKE_FIRST_RUN=0`.

Outputs:
- `shell/target/release/bundle/macos/HillAudiobook.app`
- `shell/target/release/bundle/dmg/HillAudiobook_<version>_aarch64.dmg`

CI (`.github/workflows/check.yml`, job `macos-app`) runs this on a `macos-14` (Apple Silicon) runner and uploads the `.dmg`.

On Linux, `scripts/build_engine.sh` builds the same frozen engine. `scripts/smoke_engine.py` checks it, and the shell builds and runs `--smoke-test` with `HILLAUDIO_ENGINE=/path/to/hillaudio-engine`. That is how the packaging is tested outside macOS.

## Signing and notarization ("signed-ready")

Without credentials the app is ad-hoc signed. It runs on the machine that built it and for testing; Gatekeeper blocks it elsewhere (right-click → Open to override).

To sign for distribution, set these before `scripts/build_macos.sh`:

| Variable | What |
|---|---|
| `APPLE_SIGNING_IDENTITY` | A "Developer ID Application: … (TEAMID)" identity in the keychain. Used for the engine's files (step 3) and by Tauri for the app. |
| `APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD` | Optional, for CI: the base64 `.p12` and its password; Tauri imports it into a temporary keychain. |
| `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID` | Notarization with an Apple ID. `APPLE_PASSWORD` is an app-specific password. |
| `APPLE_API_ISSUER`, `APPLE_API_KEY`, `APPLE_API_KEY_PATH` | Or notarization with an App Store Connect API key. |

With an identity set, Tauri signs the app with the hardened runtime and `shell/src-tauri/entitlements.plist`. With either set of notarization variables it also submits the app to Apple's notary service and staples the ticket. The script then runs `codesign --verify --deep --strict` and `spctl --assess`. See Tauri's guide: https://v2.tauri.app/distribute/sign/macos/

### Entitlements (D-057)
- **Hardened runtime: on.** Required for notarization.
- **`com.apple.security.cs.allow-unsigned-executable-memory`:** Python and the speech runtime (ctranslate2/onnxruntime) need writable-executable memory.
- **`com.apple.security.cs.disable-library-validation`:** the engine loads the Python extension modules bundled inside it.
- **`com.apple.security.network.client`:** loopback to the engine, and the user-started model download.
- **No App Sandbox.** Projects refer to recordings and manuscripts wherever they are (reference mode, never copied or moved). A sandboxed app would need a security-scoped bookmark for every one of them. Mac App Store distribution would need that work; Developer ID distribution doesn't.

## Platform notes
- Minimum macOS 12 (Monterey), set in `tauri.conf.json`.
- The CI build is Apple Silicon only. An Intel build is the same script on an Intel Mac (or `macos-13`); a universal build would need universal Python wheels. See open question 5 in PROGRESS.
- The app home is `~/Library/Application Support/HillAudiobook`. It holds settings, recent projects and models (`models/`). An existing `~/.hillaudiobook` keeps being used. `$HILLAUDIO_HOME` overrides both.
- Size: the engine is ~0.5–0.6 GB (ctranslate2, onnxruntime, PyAV, numpy). The default small.en model (486 MB) is downloaded on first run.

## Licensing before distributing
See `docs/LICENSES.md`.
- The bundled ffmpeg is our own LGPL build: separate programs, with the license texts and build configuration beside them in `bin/`.
- For a public release, LGPL compliance also needs the corresponding source on offer: the FFmpeg and LAME tarballs plus `scripts/build_ffmpeg.sh`.
- A full third-party notices file is part of release preparation.
