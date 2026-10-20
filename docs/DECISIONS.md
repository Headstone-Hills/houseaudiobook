# Decisions

A running log of choices the spec doesn't make. Newest last. "Provisional" means
there is a named milestone and evidence that would revisit it.

## D-001 · Separate repository (2026-10-20)
HillAudiobook lives in its own repo, not in `hill-apps` (the handheld music apps),
by the user's choice. It shares no code or toolchain with them.

## D-002 · Fixtures: espeak-ng by default, 16 kHz mono, committed (2026-10-20)
`scripts/make_fixtures.py` uses espeak-ng by default and macOS `say` with `--tts say`.
- espeak-ng runs the same on macOS (`brew install espeak-ng`) and Linux, so
  `make fixtures` reproduces the committed files byte for byte with the same
  espeak-ng version (1.51 here). Determinism was verified by generating twice and
  comparing hashes.
- 16 kHz mono 16-bit is what Whisper consumes, and keeps the committed set at
  2.1 MB (66 s). Higher sample rates for M1 ingest tests are generated inside the
  tests, not committed.
- The text is a short adaptation of two Aesop fables (public domain).

## D-003 · Fixture ground-truth semantics (2026-10-20)
- Timestamps are exact by construction: the generator places every clip and gap
  and records integer sample offsets. TTS padding is trimmed first, so our gaps
  define the pauses.
- Removal-type events (`stutter`, `false_start`, `commentary`) span exactly the
  region a perfect cleanup would cut, including the pause after it. Cutting that
  span leaves the following clean read intact. Detection precision/recall in later
  milestones is scored against these spans.
- The scenario: 2 stutters, 1 false start, 1 commentary, 1 misread, 1 skipped
  word, 2 overlong silences (3.5 s, 2.6 s), 2 take groups (one cross-file pickup,
  one in-file repeat), and noise at −65 and −50 dBFS RMS (the −50 file is meant
  to fail ACX's noise-floor rule later).
- Suggested preferred take = the latest correct read, matching how pickups work.

## D-004 · Python 3.11 venv (2026-10-20)
`make setup` builds the venv with `python3.11` (override with `PYTHON=`), so
development runs on the spec's minimum version, and newer-only syntax fails early.

## D-005 · Test client transport: httpx2 (2026-10-20)
Starlette 1.9 deprecates `httpx` for `TestClient` in favour of `httpx2`
(BSD-3-Clause), so the dev extra depends on `httpx2`.

## D-006 · Ports and dev proxy (2026-10-20)
The engine binds `127.0.0.1:8765` only. Vite serves `127.0.0.1:5173` and proxies
`/api`, including WebSocket upgrades for job progress, so the UI only ever uses
same-origin relative URLs. The native shell can then load the same build.

## D-007 · ASR backend: faster-whisper default (provisional; revisit in M3)
See `docs/spikes/01-asr.md`. faster-whisper runs on every platform, has word
timestamps and VAD built in, and needs no torch. mlx-whisper is optional on Apple
Silicon if M3 shows a speed win. Whisper tends to drop disfluencies, so cleanup
will also compare acoustic speech regions with transcript coverage, and
uncertain findings stay *suggested*.

## D-008 · Alignment: own word alignment + energy snapping (provisional; revisit in M3)
See `docs/spikes/02-alignment.md`. stable-ts is not adopted because it is
archived (2026-05-30). WhisperX is deferred because of its torch/pyannote weight,
`<3.14` limit and lack of timing for numerals. Targets that would trigger
re-evaluation: boundary error at sentence edges of median ≤ 50 ms and p95 ≤ 150 ms
after snapping.

## D-009 · Shell: Tauri 2 + PyInstaller sidecar (provisional; prove in M9)
See `docs/spikes/03-shell.md`. Fallback is pywebview.

## D-010 · Milestone numbering (2026-10-20)
SPEC §3 says the shell comes in "M8", but §9 and CLAUDE.md put packaging in M9
(M8 is Finalize + export). §9 wins: the shell is M9.

## D-011 · ASR spike timing (2026-10-20)
SPEC §3 says to spike both Whisper variants "in M1", and §9 puts spikes in M0.
The desk spike happened in M0; the measured benchmark is part of M3, which is
where transcription is built, because no weights were downloadable here.

## D-012 · `store/` package for the project database (M1)
SPEC §3's layout has no home for the `.hillproj` folder and SQLite code, so it
lives in `engine/hillaudio/store/`. The schema is applied as append-only
migrations tracked by `PRAGMA user_version`, each in one transaction. A project
whose schema is newer than the app is refused rather than downgraded. Tables
arrive with the milestone that needs them; M1 adds `project_meta`,
`source_file` and `operation_log`.

## D-013 · Import: copy by default, reference optional (M1)
SPEC §4 allows sources to be "copied or referenced".
- **Copy is the default**, so a project is self-contained and survives the
  user tidying their recordings folder. Copies are named `<sha256[:12]>_<name>`,
  hash-verified after copying, and set read-only.
- **Reference** keeps very large files in place.
- Either way the original is only read. Re-importing identical content (same
  hash) returns the existing source with a warning instead of duplicating it.
- Labels default to the file stem and are metadata only.

## D-014 · Exact lengths and peak layout (M1)
- `num_frames` and `duration` come from decoding, not from ffprobe's container
  estimate. This makes MP3 lengths exact: a 2.000 s MP3 test file decodes to
  exactly 88,200 frames, because ffmpeg trims the encoder delay and padding.
- Peaks are one envelope across channels, at 512 × 4ⁿ frames per bucket (4
  levels), stored as int16 in `cache/peaks/<sha256>.<settings>.npz`. A 10-hour
  44.1 kHz file needs about 6 MB at the finest level. The cache is regenerated
  on demand if deleted.

## D-015 · One open project at a time (M1)
The engine holds a single open project. Creating, opening or closing one is
refused (409) while jobs are running, so a job never writes into a project the
UI has moved away from.

## D-016 · Browser end-to-end test, and the bugs it found (M1)
`make e2e` runs Playwright against the real engine and Vite (`web/e2e/`). It's
kept out of `make check` because it needs Chromium and takes longer, and CI
runs it as its own job. On its first run it found two bugs the unit and API
tests could not:
1. uvicorn had no WebSocket library, so `/api/jobs/ws` was a 404 on the real
   server. Starlette's TestClient handles WebSockets without one. Fixed by
   depending on `websockets` (BSD-3-Clause), rather than `uvicorn[standard]`,
   to keep native dependencies down for packaging. Regression:
   `tests/test_server.py` serves the app with real uvicorn.
2. Parallel import jobs numbered sources in finish order, not the order the user
   picked. Fixed with serial job lanes (D-019).

Driving the dev servers also found a third bug:
3. An idle job WebSocket handler never noticed its client had left, which leaked
   handlers and hung uvicorn's shutdown and auto-reload. Fixed by awaiting
   `receive()` alongside the sender. Regression: `tests/test_server.py`, which
   was confirmed to fail on the old code.

## D-017 · Localhost guard (M1)
Binding to 127.0.0.1 doesn't stop other web pages in the user's browser from
calling the engine (CSRF), or from reaching it through DNS rebinding. Either
could import or read local files, which would breach the local-only rule. So
`api/guard.py` refuses:
- any request whose Host isn't a loopback name;
- any request that carries an Origin header not on the allowlist (the Vite dev
  server, the engine itself, Tauri's origins, plus `HILLAUDIO_ALLOWED_ORIGINS`).

Requests without an Origin header (curl, the native shell's own process) pass.

## D-018 · Upload as a raw PUT body (M1)
Browsers don't expose file paths, so drag-and-drop and the file picker stream
the file to `PUT /api/project/sources/upload?filename=…`. The body goes to
`cache/uploads/`, is imported in copy mode, and that temp file is removed. A raw
body avoids a multipart dependency and an extra spooled copy. The client uses
XHR so it can show upload progress for multi-gigabyte files. Import by path
avoids the upload entirely.

## D-019 · Serial job lanes (M1)
`JobManager.submit(..., lane="import")` runs jobs in a lane one at a time, in
submission order. Imports use the "import" lane, so source order follows the
user's selection and big copies don't compete for the disk. Verification and
other jobs use the shared pool.

## D-020 · App settings location (M1)
The recent-projects list lives in `$HILLAUDIO_HOME/recent.json` (default
`~/.hillaudiobook`). It holds paths and names only. The default project
location, `~/HillAudiobook Projects`, is created on first use. Any other
location must already exist, so a typo can't silently create folders.

## D-021 · Verify reports, never repairs; relink requires identical content (M1)
Opening a project re-hashes every source in a background job and marks each
one `ok`, `missing` or `modified`. Nothing is changed automatically. Relink
accepts only a file with the recorded hash. For a copied source, a damaged
copy is renamed `*.damaged-<timestamp>` and kept, never deleted, before the
good file is copied back in.
