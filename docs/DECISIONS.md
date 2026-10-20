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

## D-022 · Manuscript formats and libraries (M2)
- **PDF: pypdf** (BSD-3-Clause, pure Python). Its layout mode keeps the
  indentation and blank lines that paragraph detection needs, and it reads PDF
  page labels (printed numbers like "iii", "1"). It takes about 4 ms a page
  (measured: 1.1 s for a 300-page book), so preview is a plain request.
  PyMuPDF was rejected: AGPL-3.0.
- **.docx: the standard library** (`zipfile` + `xml.etree`) rather than
  python-docx, avoiding an lxml dependency. Python's expat (2.4+) guards against
  entity-expansion attacks.
- **.txt/.md**: UTF-8 (with or without BOM), falling back to Windows-1252 with a
  warning.
- **EPUB, .doc, RTF, ODT**: refused with a message saying what to export to.
  EPUB is in SPEC §6.2 but not in M2's acceptance criteria; it's deferred.
- reportlab (BSD) is a dev-only dependency, used to generate PDF fixtures.

## D-023 · One manuscript per project; re-mapping replaces units (M2)
- A project holds one manuscript. Importing another replaces it in the
  database; the previous copy stays in `text/`, never deleted.
- The original is copied into `text/` (hash-verified, read-only), and every
  later re-mapping reads that copy. Opening a project verifies the copy's hash
  along with the audio sources.
- Re-mapping (new page settings) replaces all `manuscript_unit` rows. Later
  milestones must not key anything durable on unit ids. M4's stable issue keys
  will use text and position instead.

## D-024 · Page model (M2)
- Extractors produce one model: plain text plus *page marks* (char offsets
  where a page begins). Numbering and units are computed from that, so every
  format behaves the same. A mark is recorded at the next text written, so
  blank pages count toward physical numbering without creating empty pages.
- Each unit stores its start page and end page (a sentence can turn a page),
  the printed number (NULL when not numeric, e.g. "iii"), a display label, and
  estimated and front-matter flags.
- Modes, preferred in this order by "auto": text markers (`[p. 12]`, regex
  configurable), explicit breaks (form feeds; .docx page, `pageBreakBefore`
  and new-page section breaks), PDF pages (with labels if present), else a
  words-per-page estimate. Estimates default to 250 words a page, a typical
  trade paperback, and are labelled "approx. N" (SPEC §6.2).
- Text before the first marker belongs to the page before it, or is front
  matter if that marker is "[p. 1]".
- "Printed page 1 is physical page N" sets the offset; earlier pages are front
  matter. For a PDF with labels, front matter is whatever comes before the
  first numeric label.
- Skipping front matter works per sentence, not per paragraph, because a
  paragraph can run from the last front-matter page onto page 1.
- Word's last-rendered page breaks (`lastRenderedPageBreak`) are ignored. They
  reflect one machine's layout at save time, so a .docx without explicit
  breaks uses the estimate.

## D-025 · PDF text heuristics (M2)
- **Running heads and feet** are lines repeating at the top (or bottom) of at
  least 30% of pages (and 3+ pages), compared with digits masked. Bare page
  numbers are always dropped. A line that is the only text on its page is
  never furniture, so a title page survives even when the running head repeats
  the title.
- **New paragraph** at a blank line, an indent of more than 2 columns beyond the
  page's margin (the margin most pages share, for pages under 3 lines), a
  chapter heading, or after a short line (under 80% of the median) that ends a
  sentence.
- **Hyphenation**: "jump-" at a line end followed by a lowercase letter is
  rejoined ("jumping"). Known cost: a compound split exactly at its hyphen
  ("well-" / "known") becomes "wellknown". That is rare, and M3's text
  normalization must treat hyphens as optional when comparing (SPEC §6.3
  lists hyphenation), which makes it harmless.
- A paragraph continues across a page turn, with the page mark inside it.

## D-026 · Sentences and chapters (M2)
- **Sentence boundaries**: terminal punctuation, optional closing quotes or
  brackets, whitespace, then an uppercase letter, digit or opening quote. This
  keeps `"Stop!" she cried.` and `wait... then` together. A period after a
  listed abbreviation (Mr, Dr, St, etc.) or a single initial is not a boundary.
- **Headings** are never split ("Chapter One. The Fox and the Grapes." is one
  utterance). A paragraph is a chapter heading if it's styled as one (.docx
  style, including localized names via styles.xml or an outline level; a
  Markdown `#`), or if it reads like one: "Chapter 3", "CHAPTER XII",
  "Part Two", "Prologue". "Part of the plan" is not one.

## D-027 · The UI shows the engine's open project on load (M2)
Regression found by e2e: an update about already-finished jobs made the UI
fetch the engine's open project and switch to it, even mid-typing on the
Projects screen. Now the UI asks once on load (`GET /api/project`, which
returns `null` when none is open, so it isn't an error) and continues in that
project, which makes a page reload safe. Job updates only refresh a project
that's already on screen.

## D-028 · ASR model and settings, measured (M3)
`docs/bench/asr-2026-10-20.md` closes spike 01 and confirms D-007/D-008:
- **Backend: faster-whisper, `small.en`, int8 on CPU, beam 5, word timestamps,
  language en**, with a **disfluency `initial_prompt`** ("Um, uh, the- the, I mean,
  sorry, let me start again."). Without the prompt, every model tested dropped every
  injected stutter; with it, small/medium/turbo kept them. small.en + prompt had 0.0%
  verbatim WER at 0.19x real time.
- Words whose text normalizes to nothing (Whisper emits "-" after a stutter) are kept
  in the stored transcript but ignored for timing and alignment.
- Boundaries are snapped with analysis/boundaries.py, using hysteresis thresholds and
  neighbour bounds. Two failure modes found by the benchmark, each with a regression
  test: quiet word endings clipped, and a start snapped into the previous (stutter)
  word. Snapped p95: 10 ms (start), 12 ms (end).
- Model weights download on first use into `$HILLAUDIO_HOME/models`. This is the only
  network use, it carries no user audio or text, and it needs huggingface.co and
  *.hf.co allowed in the environment.
- Caveat: synthetic voice and a small sample. Re-check on a real narration recording
  before M8.
