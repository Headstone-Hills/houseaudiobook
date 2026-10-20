# Progress

## M0 · Scaffolding & spikes: done (2026-10-20)

Acceptance criteria (SPEC §9):
- [x] Repo layout per SPEC §3: `engine/hillaudio/{ingest,text,asr,analysis,edl,render,guide,api}`, `engine/tests`, `engine/fixtures`, `web/src/{pages,components,state}`, `shell/`, `scripts/`
- [x] CI-style `make check`: ruff (lint + format), mypy `--strict`, `tsc --noEmit`, ESLint (`--max-warnings 0`), pytest, Vitest. Also run by `.github/workflows/check.yml`.
- [x] `make setup`, `make dev` (engine :8765 + Vite :5173 with `/api` proxy; smoke-tested with `curl` through the proxy), `make test`, `make fixtures`
- [x] Fixture generator `scripts/make_fixtures.py` with ground truth (D-002, D-003); deterministic, verified by double generation + hash compare
- [x] Decision log `docs/DECISIONS.md`; licenses `docs/LICENSES.md`
- [x] Spike results: `docs/spikes/01-asr.md` (Whisper variant), `02-alignment.md` (alignment lib), `03-shell.md` (Tauri vs fallback)

Tests (engine 15, web 2):
- Fixture generator, pure parts with a fake TTS: manuscript pages, text edits, trimming, scenario counts, exact span math, removal spans abut their restart, take groups, noise level ±0.5 dB, determinism
- Committed fixtures: manuscript current, ground truth matches scenario, WAV headers match truth, silence regions at injected noise level ±1 dB, speech ≥ 20 dB above noise
- Session-wide guard: fails if any test modifies a file in `engine/fixtures/`
- API health; web app shows engine up / down

Deferred / caveats:
- The spikes are desk research. No ASR model weights could be downloaded in the
  dev container (HuggingFace is blocked by its network policy), and macOS-only
  tools (mlx-whisper, `say`, Tauri signing) can't run on Linux. The measured
  benchmark is an M3 exit criterion (spike 01).
- The CI workflow has not run yet: the GitHub repository doesn't exist yet.
- No source-import hash test yet (nothing imports sources until M1). The
  session-wide fixture guard is in place now; M1 adds a per-import test.

## M1 · Project + ingest: in progress

Plan:
- **Files.**
  - `engine/hillaudio/store/`: SQLite schema with migrations, plus `Project` create/open and the folder layout.
  - `ingest/`: hashing, ffprobe probe, streaming ffmpeg decode, import (copy or reference), verify and relink.
  - `analysis/peaks.py`: a pure, streaming multi-resolution peak builder.
  - `api/`: project/source/peaks endpoints, a job runner with WebSocket progress, and a localhost origin guard.
  - `web/`: Projects screen (recent, new, open), project screen (source table, import by picker, drag-and-drop or path, job progress, waveform).
- **Tests.**
  - Hashing.
  - ffprobe parsing, both canned JSON and real WAV/MP3.
  - Peaks: chunking doesn't change results, and levels stay consistent.
  - Project create/open/reopen.
  - Import modes, duplicates, rejecting unsupported formats, the mixed-sample-rate warning.
  - Verifying on open: missing, modified, relink.
  - **Source immutability:** bytes and mtime unchanged after a full workflow.
  - Jobs: progress and cancel.
  - API integration including upload and WebSocket.
  - Web: function-dependency logic, screens, peak drawing math.
- **Risks.**
  - Peak cache size on 10 h projects (int16 peaks, 512-sample base).
  - Exact MP3 duration (taken from the decoded frame count, not ffprobe).
  - A browser can't see file paths, so drag-and-drop has to upload.
  - Any web page can send requests to the localhost API, so the origin guard matters.

## Open questions for the user (SPEC §13 and found in M0)
Defaults are assumed until answered; none blocks M1.
1. Typical project size (hours, number of takes)? *Assumed: SPEC §8's 10 h / ~2 GB as the upper bound.*
2. Primary export target ACX/Audible or general? *Assumed: ACX preset as default, custom presets allowed.*
3. Most common manuscript formats? *Assumed: SPEC §9 order (.txt, .docx, .pdf).*
4. Guide timestamps reference original recordings, edited master, or both? *Assumed: selectable, default original recording.*
5. Minimum macOS version; Apple Silicon only or Intel too? *Affects D-007 (mlx) and M9.*
6. MIDI / music-bed track ever needed? *Assumed: no (SPEC §2 non-goal).*
7. **pedalboard is GPL-3.0.** Accept GPL terms for the shipped app, or implement compressor/limiter/gate ourselves (numpy) or with ffmpeg filters? Needed before M8.
8. **Commercial use?** If the app will ever be sold or used commercially, CrisperWhisper's weights are off the table, which D-007 already assumes.
