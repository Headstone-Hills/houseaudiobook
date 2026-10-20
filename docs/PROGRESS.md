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

## M1 · Project + ingest: done (2026-10-20)

Acceptance criteria (SPEC §9):
- [x] **Create/open `.hillproj`**: `store/project.py`, API `POST /api/projects`, `/api/projects/open`, Projects screen (recent, open, new with function checkboxes and prerequisite handling). Tests: `test_project.py`, `test_api.py`, `App.test.tsx`.
- [x] **Import `.mp3`/`.wav`**: copy (default) or reference, by path, file picker or drag-and-drop upload; extension must match content; per-file errors don't stop a batch; duplicates detected by hash; mixed-sample-rate warning. Tests: `test_import.py`, `test_probe.py`, `test_api.py`.
- [x] **Hash, probe, peaks**: streaming SHA-256; ffprobe metadata; exact length from decoding; multi-resolution peaks cached by hash + settings, regenerated if the cache is cleared. Tests: `test_peaks.py` (block size doesn't change results), `test_api.py` (paging, regeneration).
- [x] **Sources immutable**: `test_source_immutability.py` runs every M1 workflow through the API (copy, reference, duplicate, upload, peaks, cache rebuild, reopen-and-verify, relink) and proves each original's bytes, size, mode and mtime are unchanged. It was checked by planting an `os.utime` in the importer; the test failed as it should. Copies are read-only. Verify-on-open detects missing and modified files; relink needs identical content; damaged copies are kept, not deleted.
- [x] Background jobs with progress and cancel over WebSocket (`test_jobs.py`, `test_server.py` on real uvicorn); cancelling an import rolls back cleanly.
- [x] End-to-end: `make e2e` (Playwright, real engine + browser), plus a manual run with screenshots.

`make check`: ruff, ruff format, mypy --strict, tsc, ESLint, 80 engine tests, 13 web tests. `make e2e`: 1 test.

Bugs found and fixed, each with a regression test (D-016): no WebSocket support on the real server; import order following finish order; leaked WebSocket handlers blocking shutdown and reload.

Deferred:
- Playback proxies, until M4 (jump-to-audio) needs playback.
- Renaming and reordering sources, part of Organize and Label (§6.3 step 2).
- Per-channel waveform display.
- Zoomable waveform, in M7.
- Recent-project removal UI.

Risks:
- Peak paging relies on JSON. A 10-hour hi-res overview is about 2–3 requests, which is fine. The zoomable timeline (M7) may want a binary format.
- The upload path stores a temporary copy before importing, so disk use temporarily doubles for that file. Import by path avoids it.
- Hash verification on open reads every source (≈2 GB for a 10 h project). It runs in the background and doesn't block the UI, but it costs disk I/O. A size+mtime fast path could be added if this proves slow (SPEC says "hash-verified on every open", so not changed yet).

## M2 · Text import & page mapping: not started

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
