# Licenses

Every dependency and model is checked before bundling (SPEC §12). Versions are
what is installed or evaluated as of the date shown. "Dev only" means it is never
shipped in the app.

## In use (updated M1, 2026-10-20)

| Package | Version | License | Shipped? |
|---|---|---|---|
| fastapi | 0.156.0 | MIT | yes |
| uvicorn | 0.54.0 | BSD-3-Clause | yes |
| websockets | 17.3 | BSD-3-Clause | yes (WebSocket support for uvicorn) |
| numpy | 2.4.x | BSD-3-Clause (plus 0BSD, MIT, Zlib, CC0 parts) | yes |
| soundfile | 0.14.0 | BSD-3-Clause; bundles libsndfile (LGPL-2.1) | yes |
| react, react-dom | 19.3.1 | MIT | yes (in the web build) |
| vite | 8.3.4 | MIT | dev only |
| typescript | 6.0.3 | Apache-2.0 | dev only |
| eslint, typescript-eslint | 10.13 / 8.72 | MIT | dev only |
| vitest, jsdom, testing-library | 5.0 / 29.1 / 16.3 | MIT | dev only |
| @playwright/test | 1.64.1 | Apache-2.0 | dev only (e2e) |
| @types/node | current | MIT | dev only |
| pytest, ruff, mypy, httpx2 | current | MIT / MIT / MIT / BSD-3 | dev only |
| espeak-ng | 1.51 | GPL-3.0-or-later | dev only (fixture generation); never bundled |
| ffmpeg | 6.1 (system) | LGPL-2.1+ or GPL, depending on build | not bundled yet; decide in M9 |

## Candidates evaluated (not yet installed in the project)

| Package / model | Version | License | Note |
|---|---|---|---|
| faster-whisper | 1.2.1 | MIT | D-007 default ASR |
| ctranslate2 | 4.8.3 | MIT | faster-whisper runtime |
| mlx-whisper | 0.4.3 | MIT | optional Apple Silicon backend |
| Whisper model weights (OpenAI) | n/a | MIT | to confirm on each model card before first-run download (M9) |
| rapidfuzz | 3.14.6 | MIT | manuscript alignment (M3) |
| stable-ts | 2.19.1 | MIT | not adopted: archived |
| whisperx | 3.8.6 | BSD-2-Clause | deferred |
| CrisperWhisper weights | n/a | Nyra Health Non-Commercial Research License | **not usable commercially** without a separate license |
| pyloudnorm | 0.2.0 | MIT | M8 |
| noisereduce | 3.0.3 | MIT | M8 |
| **pedalboard** | 0.9.26 | **GPL-3.0** | M8. Shipping it would put the bundled app under GPL-3.0 terms. Needs a decision before M8 (see PROGRESS.md, open questions). |
| tauri | 2.12.4 | MIT or Apache-2.0 | M9 |
| pyinstaller | 6.22.3 | GPL-2.0-or-later with bootloader exception | M9; the exception permits bundling non-GPL apps |
| pywebview | 6.2.1 | BSD-3-Clause | M9 fallback |

Sources: each package's PyPI or npm metadata, read on 2026-10-20 (e.g.
`https://pypi.org/pypi/<name>/json`), plus installed package metadata.
