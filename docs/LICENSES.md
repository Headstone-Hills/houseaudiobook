# Licenses

Every dependency and model is checked before bundling (SPEC §12). Versions are
what is installed or evaluated as of the date shown. "Dev only" means it is never
shipped in the app.

## In use (updated M8, 2026-10-21)

| Package | Version | License | Shipped? |
|---|---|---|---|
| fastapi | 0.156.0 | MIT | yes |
| uvicorn | 0.54.0 | BSD-3-Clause | yes |
| websockets | 17.3 | BSD-3-Clause | yes (WebSocket support for uvicorn) |
| pypdf | 6.21.1 | BSD-3-Clause | yes (PDF manuscripts) |
| rapidfuzz | 3.14.6 | MIT | yes (alignment fallback) |
| faster-whisper | 1.2.1 | MIT | yes (`asr` extra) |
| ctranslate2 | 4.8.3 | MIT | yes (faster-whisper runtime) |
| onnxruntime | 1.31.0 | MIT | yes (faster-whisper dependency) |
| av (PyAV) | 18.1.0 | BSD-3-Clause; **bundles FFmpeg libraries (LGPL)** | yes (faster-whisper decodes audio with it); check its FFmpeg build's license before M9 bundling |
| tokenizers | 0.23.3 | Apache-2.0 | yes (faster-whisper dependency) |
| huggingface-hub | 2.2.0 | Apache-2.0 | yes (model download) |
| Whisper weights: Systran/faster-whisper-small.en (default), tiny.en | n/a | MIT (model cards, checked 2026-10-20) | downloaded on first use, never bundled or committed |
| reportlab | 5.0.1 | BSD | yes (narrator guide PDF, M8; also generates PDF fixtures) |
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
| ffmpeg | 6.1 (system) | LGPL-2.1+ or GPL, depending on build | not bundled yet; decide in M9. M8 encodes MP3 with its libmp3lame (LAME is LGPL-2.0+): the bundled ffmpeg must include it |

## Candidates evaluated (not yet installed in the project)

| Package / model | Version | License | Note |
|---|---|---|---|
| ctranslate2 | 4.8.3 | MIT | faster-whisper runtime |
| mlx-whisper | 0.4.3 | MIT | optional Apple Silicon backend |
| Other Whisper weights (medium.en, large-v3-turbo) | n/a | to confirm on each model card before offering them in M9 (the turbo card lookup failed on 2026-10-20) | optional models |
| python-docx | 1.2.0 | MIT | not adopted: .docx is read with the standard library (D-022) |
| PyMuPDF | 2.0 | AGPL-3.0 or commercial | **not adopted** (D-022) |
| stable-ts | 2.19.1 | MIT | not adopted: archived |
| whisperx | 3.8.6 | BSD-2-Clause | deferred |
| CrisperWhisper weights | n/a | Nyra Health Non-Commercial Research License | **not usable commercially** without a separate license |
| pyloudnorm | 0.2.0 | MIT | not adopted in M8: needs scipy; ACX measures RMS, not LUFS (D-050) |
| noisereduce | 3.0.3 | MIT | not adopted in M8: needs scipy and matplotlib; own spectral gating instead (D-050) |
| **pedalboard** | 0.9.26 | **GPL-3.0** | **not adopted** (D-050): the finalize DSP is our own numpy code, so the GPL question doesn't arise |
| tauri | 2.12.4 | MIT or Apache-2.0 | M9 |
| pyinstaller | 6.22.3 | GPL-2.0-or-later with bootloader exception | M9; the exception permits bundling non-GPL apps |
| pywebview | 6.2.1 | BSD-3-Clause | M9 fallback |

Sources: each package's PyPI or npm metadata, read on 2026-10-20 (e.g.
`https://pypi.org/pypi/<name>/json`), plus installed package metadata.
