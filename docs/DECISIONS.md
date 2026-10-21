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

## D-029 · Alignment design (M3)
`analysis/align.py`, pure. Narration isn't a clean read, so there is no single
global alignment:
- **Anchors**: 3-token runs equal in transcript and manuscript, skipping 3-grams
  that occur more than 4 times in the manuscript (ambiguous).
- **Chaining** (DP over anchors, 64 lookback): continuing costs one per token of
  drift (edit-distance-like); any other move is a *jump* (3.5). Up to 3 extra
  words stay inside a sentence; longer insertions (false starts, commentary)
  and skips of more than 3 words become jumps, i.e. new runs.
- **Word alignment** is edit-distance DP between consecutive anchors (linear in
  practice). Ties prefer, in order: match, missing, extra, substitution, so two
  unrelated words are never paired just because it costs the same (regression:
  "now" of a skipped line was paired with "then" of the next).
- **Run edges** extend to sentence boundaries, allowing mismatches, so a misread
  last word stays with its sentence. They never cross a pause of 0.35 s or more,
  and never extend a run that a restart re-reads (that run is a false start).
  A run that reaches 3 or fewer tokens into a sentence the next run re-reads gives
  those tokens back (an anchor matching across a take boundary).
- **Fallback**: an unaligned stretch whose text is at least 85% similar
  (rapidfuzz ratio) to a short sentence is aligned to it. A two-word pickup can't
  form a 3-token anchor.

## D-030 · What a reading and its differences mean (M3)
- A **reading** is one run's pass over one sentence: coverage, score
  (`matches / max(ops, sentence length)`), and differences. A *complete* reading
  covers the whole sentence; a partial one before a restart is a **false start**.
- **Differences**: missing, extra, substitution ("misread" in the UI),
  transposition (adjacent swap), reordered (a stretch said elsewhere in the same
  sentence), and mispronunciation (a match with ASR confidence under 0.5: a
  suspicion, not counted in the score).
- **Stutters** are an "extra" difference on the sentence they repeat ("The. The
  vine..."). So a restart is either a partial reading (4+ words) or an extra
  difference (up to 3 words); M5 cleanup handles both.
- **Off-script words** between sentences that don't repeat the next sentence's
  opening belong to no sentence: they're *unaligned* (stored as
  `unaligned_segment`), so a clean reading isn't marked down for "sorry".
- A reading's times span the sentence proper. Extra words at its edges are
  differences with their own times, not part of the take.
- A sentence's **best take** is complete, has the fewest text errors, then the
  highest score. A sentence is "clean" if its best take has no text errors
  (extra words don't count; they are cleanup items).

## D-031 · Pipeline and results (M3)
- Stages run as one job on a serial `asr` lane (transcription saturates the CPU).
  By default they are the project's enabled functions (Transcribe,
  Compare to Text). Each run is an `analysis_run` row (running, done, failed or
  cancelled); the latest completed Compare to Text run is the one shown.
- Transcripts are cached in `cache/transcripts/<sha256>.<settings>.json`, and the
  10 ms energy envelope used for boundary snapping in `cache/envelope/`. Both
  are regenerable.
- Re-mapping manuscript pages replaces units, so their readings cascade away and
  unaligned segments are cleared. `project_meta.units_version` increments on
  every rewrite, and each run records the version it compared against. A run
  against an older version is reported `stale`, and the UI asks for a re-run
  instead of showing empty results. Re-running is quick because transcripts are
  cached. (Timestamps were too coarse: a run and a re-map can share a second.)
- Model sizes shown before the first download: tiny.en 75 MB, base.en 141 MB,
  small.en 464 MB, medium.en 1.5 GB, large-v3-turbo 1.6 GB (measured).

## D-032 · Testing speech without a model (M3)
- `tests/fakeasr.py` builds a transcript from the fixture ground truth: heard
  words spread over each event's true span, optionally with seeded noise
  (random wrong words, dropped stutters). Alignment tests are exact with perfect
  ASR. With 5-20% noise, at least 95-99% of sentences are found and every
  injected text error is detected in all 20 seeds.
- The browser e2e engine (`tests/e2e_app.py`) injects that fake, so CI never
  downloads a model.
- `tests/test_real_model.py` (opt-in, `HILLAUDIO_MODEL_TESTS=1`) runs small.en
  through the API pipeline. It passed on 2026-10-20: exactly the injected
  misread and skip flagged, both stutters found, the commentary unaligned.
- `scripts/bench_asr.py` compares models (ASR quality and Compare to Text
  precision/recall); see docs/bench/.

## D-033 · What becomes an issue (M4)
- **Flub:** a sentence whose best take (complete, fewest text errors, highest
  score) still has a text error: missing, substitution, transposition,
  reordered, or an extra word that is not a repeat.
  - One issue per sentence, not per take. If any take is clean, nothing needs
    fixing: the take choice is M6's.
  - A sentence with only false starts is a flub ("never read to the end").
- **Unread line:** a sentence with no reading at all.
- **Repeats are not flubs.** An extra word block equal to the manuscript words
  right after or right before it (a stutter or a restart) is tagged `repeat`.
  It is left to cleanup (M5).
- **Severity:**
  - high: real text errors and unread lines;
  - medium: long pauses;
  - low, shown as "Check": every error looks like the recognizer, not the
    narrator. That means heard confidence < 0.6, or a substitution with spelling
    ratio ≥ 75, or the same sound key (a small English phonetic key, e.g.
    "laughed"/"left").
  - Benchmark (docs/bench): small.en has 0 false issues. Every other model's
    false issues are mostly "Check".
  - Low issues stay listed. Hiding them could hide a real misread.

## D-034 · Stable issue keys (M4)
- key = sha256(kind, context)[:24].
  - For text issues the context is the normalized text of the sentence and its
    two neighbours, so a line repeated elsewhere in the book gets its own key.
  - For pauses it is the source's content hash plus the sentences on either
    side (or a 5 s time bucket when none aligned).
- Not unit ids or page labels: re-mapping pages replaces units, and the same
  issue must be found again. `manuscript_unit_id` is ON DELETE SET NULL. The
  issue keeps its text and is re-linked on the next run.
- Not exact times: a retranscription shifts them slightly.

## D-035 · Long pauses (M4)
- Pauses are gaps between speech in the energy speech mask (D-029 snapping).
  Leading and trailing room tone of a file is not a pause.
- The boundary type comes from the aligned sentences on either side:
  - different chapter units: chapter;
  - different paragraph units: paragraph;
  - otherwise: sentence;
  - with no alignment on one side: unknown, which uses the strictest
    (sentence) threshold.
- Defaults are 2.0 / 3.0 / 5.0 s (SPEC §6.3.6 suggests > 2.0 s), stored per
  project in `project_meta.issue_settings`, and must be ordered
  sentence ≤ paragraph ≤ chapter.

## D-036 · Re-run reconciliation (M4)
- One `issue` row per stable key; a run updates rows in place. For the kinds
  the run checked:
  - found, unknown key: new, open;
  - found, open: still open, details refreshed;
  - found, dismissed or resolved by the user: kept as the user left it
    (SPEC §4: "issues with matching keys keep their status");
  - found, auto-resolved earlier: regressed, reopened;
  - not found, open: auto-resolved, `decided_by = auto`.
- Kinds not checked are untouched. "Change" badges apply only to issues the
  latest check touched (`last_run_id`).
- Every status change a run makes is logged `auto` with from/to and reason, so
  each is reversible (reopen). User decisions and notes are logged `user` with
  their previous values.
- Checking flubs needs an up-to-date Compare to Text run. Pauses use one for
  boundary context only when it is up to date.

## D-037 · Jump-to-audio (M4)
- `GET /api/project/sources/{id}/excerpt?start&end` decodes [start − 3 s,
  end + 3 s], clamped to the file and at most 60 s, to 16-bit WAV with ffmpeg.
  Headers give the excerpt's real start and end.
- This is a preview only. Exports still render from the original sources, and
  16-bit PCM from any source is never a lossy re-encode.
- The browser plays it in one `<audio>` element with visible controls. A
  waveform playhead arrives with the timeline (M7).

## D-038 · The narration EDL as removed regions (M5)
- In M5 every edit is a removal: cleanup, pause shortening and the user's cuts.
  So the narration track is stored as *removed regions*: integer sample ranges
  per source, at the source's rate. Clips and timeline positions are derived:
  the sources in order, minus the active regions.
- Why:
  - Every edit ripples by construction.
  - An edit is one small row, so undo entries stay small even for a 10 h book.
  - Restoring material (SPEC §5) is changing a region's status or edges.
- Edits on this model:
  - A trim on a clip edge moves a region edge; dragging outward re-exposes
    removed audio.
  - A rolling edit shifts a region, so the length is unchanged.
- Timeline positions are integers at the highest source rate. They are computed
  from exact cumulative time (fractions), so there is no drift across clips.
- **Planned migration (M7):** moves, gain, fades and other tracks need
  explicit `edl_clip` rows (SPEC §4). The narration track will be
  materialized from the regions then, and its regions keep their history.

## D-039 · Undo/redo (M5)
- Each edit is one `operation_log` entry: op type, origin `auto`/`user`, and
  the affected `edit_region` rows before and after. Undo writes "before" and
  sets `reverted_at`; redo writes "after" and clears it.
- Undo stack: edit entries not reverted. Redo stack: reverted entries newer
  than the latest entry still in effect. So a new edit after an undo drops the
  redo stack without deleting the history (shown as "undone").
- Region ids are never reused, so a redo can always write its row back.
- A whole cleanup run is one undo step. Edits are refused (409) while an
  analysis job runs, so entries never interleave.

## D-040 · Cleanup detection (M5)
- **Stutter:** an extra word block the alignment tagged `repeat`. Cut from its
  onset to the next word's onset; confidence 0.9.
- **False start:** a fragment reading (coverage ≤ 0.6) whose very next reading,
  within 6 s, is the same sentence. Cut from the fragment's onset to the
  restart's onset; confidence 0.9 (0.75 if the restart is partial too).
  Longer partial readings are takes (M6) and are never cut here.
- **Commentary:** unaligned speech that is set off from the previous line by a
  pause (≥ 0.2 s). Cut from its onset to the next reading's onset.
  - Confidence 0.9 with a keyword hint ("hold on", "let me", "sorry", "again",
    "from the top"...).
  - Otherwise 0.6 for ≤ 6 words, else 0.4: a long passage may be text the
    manuscript lacks.
- Candidates never overlap; of two overlapping, the more confident is kept.
- **Without a manuscript:** immediate repeats of 1-3 words in the transcript
  alone, at confidence 0.75.
- Candidates at or above `auto_apply` (0.7) are applied as `removed`, the rest
  `suggested`. Stable keys come from kind, source hash and text context.
  - Re-running keeps every existing region's status (a restore stays restored).
  - It refreshes bounds only of regions nobody touched.
  - It never silently restores a region that isn't found again.
- The pause before unwanted speech is kept and the pause after it removed, so
  the join sounds like one natural pause.

## D-041 · Render and previews (M5)
- `edl/render.py` reads sources with ffmpeg.
  - It seeks to a whole second before the start (exact at any rate) and drops
    frames to the exact sample. No `-ss` for reads from 0: for MP3 that changes
    the encoder-delay skip.
  - Tests check reads against a full decode for WAV and MP3.
- A cut inside one recording is crossfaded over 10 ms using the audio on both
  sides (the clip handles). Recordings are joined without a blend.
  Resampled sources (mixed rates) are joined without a blend for now.
  With no edits there are no joins, hence the sample-identical round trip.
- Previews: `GET /api/project/edl/render?start&end` (timeline seconds, ≤ 60 s)
  as 16-bit WAV. Exports (M8) will render from the same code at full quality.

## D-042 · Checks on the edited timeline (M5)
- Flub checks ignore readings with less than half their span left on the
  timeline.
- Pauses are measured with cut audio left out. Issue positions stay in the
  recording, so keys and jump-to-audio are unchanged.
- "Shorten pause" cuts the pause's middle to leave `keep` seconds (default: the
  boundary's natural pause, 0.6/1.2/2.0 s). It is one region per issue, so
  shortening again adjusts the same cut.

## D-043 · Ordered segments (M6)
- Choosing a pickup from another recording must play it where its line
  belongs, which removed regions alone can't express. So the timeline is now
  an ordered list of *segments* (recording, in, out), minus the removed
  regions that fall inside them. The default is each recording once, in order,
  and it is stored as an empty `edl_segment` table.
- `place(segments, source, a, b, before)` moves [a, b) of a recording to play
  just before a given (recording, sample). With no target it goes back home.
  `normalize` merges neighbours that continue the same recording, so a move
  and its return leave exactly the original segments, and undo-all is
  sample-identical.
- This is the first step of D-038's planned migration. Gain, fades and extra
  tracks (M7) will become properties of segments and tracks.

## D-044 · Take groups and ranking (M6)
- A take is a reading of a sentence with coverage > 0.6 that isn't mostly cut
  by cleanup. Fragments are false starts (D-040). Two or more takes make a
  group, keyed by the sentence's text in context.
- Rank, most important first:
  1. complete;
  2. real text errors (not likely mishearings, D-033);
  3. likely mishearings;
  4. non-repeat extra words;
  5. loudness more than 3 dB from the narration's median (SPEC: "loudness
     consistency");
  6. alignment score;
  7. the later take (narrators re-read because the earlier one was wrong).
- The suggestion is *confident* when it wins on 1-3. Otherwise the UI says
  "listen and confirm".
- A take's span runs from its onset to the next speech onset in that recording
  (or the file's end). Cutting a take keeps the pause before it.

## D-045 · What choosing a take does (M6)
- One undoable entry holds all of it:
  - every other take is cut (a `take` region over its span) and the chosen
    take's region is restored;
  - the previous choice goes home;
  - if the chosen take is in a different recording from the line's first
    reading (in recording order), it is moved to play right before that
    reading's span;
  - the take statuses change, and the group is confirmed, remembering the
    previous choice (for "back").
- Detection applies its suggestions the same way, as one `auto` entry per run.
  Re-running keeps existing groups and choices; new takes of a known line join
  as alternates (cut).
- Alternate and rejected are both off the timeline. "Rejected" is a label
  ("don't consider this"), and the preferred take can't be rejected.
- Take cuts appear only in the Takes panel. The Removed Material panel counts
  them but doesn't offer to restore them, since that would play the line twice.

## D-046 · The multitrack model (M7)
- The **narration track** stays "ordered segments minus removed regions"
  (D-038, D-043), so cleanup, takes and the checks keep working unchanged.
  - Segments carry clip attributes: gain, fade in/out (source samples, at the
    first/last clip of the segment), mute, and a split mark that keeps two
    pieces apart.
  - `normalize` merges neighbours only when the audio continues and the
    attributes match, so undo-all returns to the default.
- **Other tracks** hold free clips (`edl_clip`, SPEC §4) at fixed timeline
  positions. They don't ripple with narration edits, as in Audacity.
- Moving a narration clip to another track cuts it from the narration (a `cut`
  region tagged with the new clip). Moving it back restores that region and
  re-inserts the piece.
- `layout()` places every clip on every track. The server render and the
  browser preview both use its output.
- Markers are anchored to a recording sample (or to a timeline position when
  placed over empty space), so they move with edits.

## D-047 · The mixer (M7)
- The render overlap-adds every audible clip, with clip gain, linear fades,
  track gain and pan.
  - A narration cut inside one recording keeps its 10 ms crossfade, done as
    overlap-add with the clip handles.
  - Contiguous audio (a split) is never blended.
- Mute and solo: with any track soloed, only soloed tracks play.
- **Pan uses a balance law.** Left = min(1, 1 - pan), right = min(1, 1 + pan):
  unity at centre, and the far side fades out.
  - Why: centre pan must not change the narration's level (no -3 dB), so
    unedited mixes stay exact.
  - The output is stereo only when a track is panned (or a source is stereo).
  - Mono sources are read as mono and copied to both sides; ffmpeg's own upmix
    is -3 dB per side.
- Unity gain and centre pan touch no samples: the round trip is exact.

## D-048 · Realtime preview from the EDL (M7)
- The browser plays `layout()`'s clips with Web Audio.
  - Each clip is fetched in 10 s chunks, on a fixed grid so re-scheduling
    reuses them, as excerpts of the original recordings (about 8 s ahead), and
    decoded in the browser.
  - The audio-node chain is source -> gain (clip x track gain, with the same
    linear fade breakpoints) -> pan (the D-047 balance law, from
    splitter/gain/merger nodes; not StereoPanner's equal-power law) -> output.
- Nothing is rendered in advance, so an edit is heard on the next Play.
- The preview leaves out the 10 ms join crossfades. "Render preview" (P) plays
  the server's exact mix of the selection (≤ 60 s).

## D-049 · Timeline interaction (M7)
- Keyboard first (CLAUDE.md): every tool has a key, listed in the panel.
  The canvas is one focusable `role="application"` region with a live status
  line.
  - Playhead: Space, ←/→ (Shift ×10), Alt+←/→ snap, Home/End.
  - Clips and tracks: Tab/Shift+Tab clip, ↑/↓ track.
  - Editing: [ ] selection, Delete ripple delete, S split, Shift+[ ] trim to
    the playhead, , . roll, Alt+Shift+←/→ move to snap, Alt+↑/↓ move to track.
  - Clip attributes: +/− gain, F/G fades, U mute clip.
  - Tracks and markers: Shift+U/Shift+S track mute/solo, M marker, T track.
  - Shift+R restores the cut at the playhead; P renders a preview.
  - View: Z/Shift+Z zoom, 0 fit, N snap on/off; ⌘/Ctrl+Z undo.
- Edits go through a queue, and each is built from the latest state, so quick
  repeats compose.
- Drops are ripple inserts: on the narration, a moved clip plays just before
  the audio under the drop point (as positioned before the move).
- Waveforms use the peak level whose bucket fits one pixel at the current
  zoom, fetched in 8192-bucket chunks as they come into view (SPEC §8).

## D-050 · Finalize DSP is our own numpy code (M8)
- Measurement follows ACX's terms: sample peak, unweighted RMS over the file,
  and noise floor as the RMS of the quietest 0.5 s window. Digital silence (a
  0.5 s window of zeros) is flagged.
- Noise reduction is spectral gating.
  - STFT of about 43 ms, Hann window, 75% overlap.
  - Gain per bin: max(floor, 1 − (1 + 2·strength)·noise/level). The floor is
    −reduction·min(1, 1.5·strength) dB, and the mask is smoothed 3×3.
  - Blocks carry two FFTs of context on each side, so block results equal one
    pass.
- Dynamics run on 10 ms frames: vectorized gain computers, then
  attack/release smoothing over frames, then linear interpolation to samples.
  - Compressor: soft knee.
  - Gate: downward expander with hold and a range limit.
  - Limiter: 1 ms frames, min over a 5 ms look-ahead, 50 ms release, then a
    final clamp that guarantees the ceiling.
- Why not pedalboard: it is GPL-3.0 (open question 7).
- Why not noisereduce/pyloudnorm: both need scipy, and noisereduce matplotlib,
  for a few hundred lines we can test exactly.

## D-051 · Presets and the ACX numbers (M8)
- The ACX preset holds:
  - RMS −23 to −18 dB; peak ≤ −3 dB; noise floor ≤ −60 dB RMS;
  - MP3 ≥ 192 kbps CBR at 44.1 kHz; the same channel format in every file;
  - 1–5 s room tone at each end (defaults: head 1 s, tail 3 s);
  - ≤ 120 min per file.
- The ACX help page (help.acx.com, "ACX Audio Submission Requirements") could
  not be fetched from this environment (DNS blocked). The numbers come from
  that page's summary in web search results on 2026-10-21, cross-checked with
  secondary guides. Secondary guides disagree on the head room tone (0.5–1 s
  vs 1–5 s), so the preset uses the summary's 1–5 s. The 120-minute limit
  appears only in secondary guides. Custom presets can override everything.
  The user confirmed the RMS, peak and noise-floor numbers on 2026-10-21 (D-058).
- Chain defaults: target RMS −20 dB (mid-range), ceiling −3.5 dB (0.5 dB under
  the peak limit, as margin for MP3 encoding).

## D-052 · Export pipeline and the report (M8)
- There are three block passes per output file (BLOCK_S = 10 s, aligned to the
  frame grids):
  1. render the section with clip-level gain riding;
  2. room tone head/tail, noise reduction, gate and compressor, written to a
     float32 temp file while measuring RMS;
  3. normalization gain and the limiter, into the encoder.
- The section's own leading/trailing silence (beyond 0.25 s before speech and
  0.5 s after) is replaced by the preset's room tone, looped from the
  room-tone clip with 50 ms crossfades. It is not digital silence.
- Encoders:
  - WAV 16-bit with TPDF dither (seeded: repeatable exports), or 24-bit.
  - MP3 via ffmpeg/libmp3lame, `-b:a` (CBR) at the preset's sample rate,
    encoded once from float PCM.
- **Never lossy to lossy:** if any clip on the timeline comes from a lossy
  source, MP3 export is refused in pre-flight and by the API (409). WAV is
  allowed.
- The report measures each file **after writing**, by decoding it back, plus
  ffprobe for codec, rate, channels and bitrate. So it describes what will be
  uploaded.
- Files go to `exports/<UTC stamp>/` with `report.json`, and the export is
  logged. A failed or cancelled export removes only its own folder.
- Chapter splits start where each chapter's first line (its heading) plays on
  the timeline. Marker splits start at the markers.

## D-053 · The narrator corrections guide (M8)
- Rows are open issues: flubs and unread lines, plus long pauses and resolved
  issues on request. Dismissed issues are never included.
- Times are in a stated reference:
  - "original": the recording the issue was found in, with its time;
  - "master": the edited timeline, or "cut from the master" if the audio is
    gone.
- Sorting is by page (manuscript order) or by time.
- Formats: CSV (with a commented preamble), a Markdown table, and a landscape
  A4 PDF (reportlab) with a header (project, narrator, date, reference) and
  counts by type.


## D-054 · The Mac app is a thin Tauri shell around `hillaudio serve` (M9)
- **One server for UI and API.** The engine serves the built web UI and the API
  from one loopback port (`hillaudio serve`). The Tauri window just opens that
  URL. So the app *is* web mode, and "web mode still works" is true by
  construction. `make serve` runs the same thing from a checkout.
- **The port.** The engine binds port 0 on 127.0.0.1 before starting uvicorn on
  that socket. This leaves no race between picking a free port and listening.
  It then prints `HILLAUDIO_READY port=N url=…`. Only loopback hosts are
  accepted (`--host 0.0.0.0` is refused). The engine's own origin is added to
  the Host/Origin guard; nothing else is.
- **Lifetime.** `--exit-with-stdin`: the shell holds the engine's stdin, and the
  engine exits when it closes. Any shell exit, including a crash or SIGKILL,
  therefore ends the engine. On a normal quit the shell closes stdin, waits 5 s,
  then kills it.
- **The launcher is its own crate.** Process handling (`shell/launcher`) has no
  Tauri dependency, so it is unit-tested with `cargo test` anywhere: READY
  parsing, finding the engine in the bundle, crash and timeout errors, stop on
  stdin close. `main.rs` only wires windows to it.
- **`--smoke-test`** makes the built app start its bundled engine, check
  `/api/health` and the UI page, and exit 0/1. CI runs it on the `.app`.
- **PyInstaller onedir, not onefile.** onefile unpacks ~0.5 GB to a temp folder on
  every launch, which is slow and leaves litter. onedir starts in about 1 s
  (0.9 s measured on Linux). The engine sits in `Contents/Resources/engine/` as a
  plain resource, not a Tauri "sidecar": a sidecar is a single binary, and this
  engine is a folder.
- **Fallback not needed.** SPEC's fallback was PyInstaller + pywebview. Tauri
  built and ran here (on Linux, against webkit2gtk), and the macOS CI job is the
  proof on the target.

## D-055 · Speech models: pinned, verified, explicit downloads (M9)
- **A fixed catalog.** `asr/models.py` lists tiny.en, base.en, small.en and
  medium.en from Systran's faster-whisper repos. Each entry pins a commit and
  records every file's size and hash, read from the Hugging Face API
  (`/api/models/<repo>?blobs=true`) on 2026-10-21:
  - SHA-256 for the LFS weights;
  - the git blob SHA-1 for small files (the only hash the API gives for those).

  A file that doesn't match is never used. Models whose license we haven't
  confirmed (large-v3-turbo) are not offered.
- **Our own downloader instead of `huggingface_hub`.** It is a few dozen lines of
  urllib and gives us what the UI needs:
  - byte-level progress;
  - cancel between chunks;
  - resume with an HTTP Range from `.part` files;
  - verification before install;
  - an `.installed.json` marker, so a half-download is never "installed".

  A truncated transfer keeps its `.part` and says it will resume. A checksum
  mismatch deletes it.
- **Nothing downloads implicitly.** Transcription loads only an installed model.
  Otherwise it fails with "isn't downloaded yet (about N MB)". The UI shows a
  first-run banner with the size and the source and a Download button, and
  analysis stays disabled until the model is there. Copies the earlier implicit
  download left in the HF cache layout, at the same pinned revision, are
  adopted rather than downloaded again.
- **Local-only still holds.** The download is the only network call. It is
  started by the user, sends nothing of theirs, and goes to a single host:
  huggingface.co, or `HILLAUDIO_MODEL_ENDPOINT` for tests.

## D-056 · Bundled tools and the app home (M9)
- **ffmpeg/ffprobe lookup.** The engine finds them in this order:
  1. `$HILLAUDIO_FFMPEG_DIR`;
  2. the copies bundled in the frozen engine (`_MEIPASS/bin`);
  3. PATH.

  The packaged app therefore never depends on what the user installed. Every
  call site goes through `hillaudio.tools`, and `/api/health` reports the path in
  use so the smoke test can require the bundled one.
- **Which ffmpeg.** Our own static build (`scripts/build_ffmpeg.sh`), not a
  third-party binary:
  - The first CI run failed because the third-party build host was down
    (HTTP 521). A single external binary host is a fragile and unverifiable
    dependency.
  - Ours is configured LGPL-only, with libmp3lame (MP3 export, D-052) and
    nothing autodetected. The build fails unless configure reports an LGPL
    license.
  - Its sources are verified: LAME by a pinned SHA-256, FFmpeg by its release
    signature from the pinned key fingerprint.
  - It passes the whole engine test suite (305 tests run with only it on the
    tool path).
  - The app needs nothing GPL, so the GPL question doesn't arise.
- **App home.** `~/Library/Application Support/HillAudiobook` on macOS, the
  platform's convention. An existing `~/.hillaudiobook` keeps being used so
  nothing is lost. `$HILLAUDIO_HOME` overrides both. Projects themselves live
  wherever the user puts them.

## D-057 · Hardened runtime, no sandbox, macOS 12+, Apple Silicon in CI (M9)
- **Hardened runtime on** (needed to notarize), with three entitlements:
  - unsigned executable memory, for Python and the ML runtimes;
  - disabled library validation, for the bundled extension modules;
  - network client, for loopback and the model download.
- **No App Sandbox.** Reference-mode projects point at recordings anywhere on
  disk, and a sandbox would need a security-scoped bookmark per file. Developer
  ID distribution doesn't require a sandbox; the Mac App Store would.
- **Signed-ready.** Signing and notarization run only when the APPLE_* variables
  are set (PACKAGING.md). Without them the build is ad-hoc signed, so CI and
  contributors can build without an Apple account.
- **Targets.** Minimum macOS 12 is the oldest still common, and Tauri 2 supports
  it. The CI runner is Apple Silicon. Intel is the same script on an Intel
  machine; it is untested (open question 5).

## D-058 · The user's answers to open questions 5, 9, 10 and 11 (2026-10-21)
- **5: Apple Silicon only.** No Intel build. The minimum stays macOS 12
  (D-057). This also leaves the Apple Silicon-only mlx backend (D-007) open for
  later.
- **9: lossy recordings export as WAV.**
  - The Export panel defaults to WAV 24-bit when any recording on the timeline
    is lossy, and says why.
  - If the user picks MP3 anyway, pre-flight and the API still refuse it:
    lossy to lossy is never done (CLAUDE.md).
  - Whether the WAV is then encoded to MP3 elsewhere is the user's call,
    outside the app.
- **10: ACX numbers confirmed.**
  - The user gave: average RMS -23 to -18 dB, peaks at or below -3 dB
    (or -3 dBTP), noise floor at most -60 dB RMS. These are exactly the
    preset's, so only its "verify on acx.com" caveat was removed.
  - Peaks are checked as sample peaks of the file as written, decoded back,
    which is the "-3 dB" reading. The limiter's -3.5 dB ceiling leaves margin.
  - A true-peak (dBTP) check would be stricter. It isn't added, since the user
    gave both readings as acceptable.
- **11: publish the ffmpeg source with each release.**
  - `scripts/build_ffmpeg.sh` keeps the exact verified inputs in `source/`: the
    FFmpeg tarball and signature, the LAME tarball, and the script.
  - CI uploads them as the `ffmpeg-source` artifact, next to the `.dmg`. They
    are not bundled in the app.

## D-059 · Chapter workspaces, and audio that covers only part of the book (2026-10-21)
The user asked to work on one chapter at a time, and for audio that covers only
part of the manuscript not to flag the rest as missing.
- **Text no recording reaches yet is "not recorded", not missing.**
  `analysis/issues.unrecorded` decides, per chapter, from the readings of the
  latest Compare to Text run:
  - Every line of a chapter nobody has read from is not recorded yet.
  - In a chapter that was read from, a run of unread lines before its first
    read line or after its last is not recorded yet if it's at least 3 lines
    (`MIN_UNRECORDED_RUN`). A line or two at an edge stays a skip: a forgotten
    chapter title, a dropped last line.
  - Unread lines between read ones are skips, however many. That's where a
    narrator loses their place.
  - The scope comes from every reading, before cuts: cutting a line later makes
    it missing, as before.
  - Not-recorded lines get no Unread line issue. The comparison marks them
    `recorded: false`, and the UI counts them apart from skipped lines.
- **Chapters are workspaces, not separate projects.** The user chose this over
  splitting the book into one project per chapter.
  - A chapter is a chapter heading in the manuscript. Text before the first
    heading is one more workspace, "Opening pages". Nothing is stored:
    `chapters.chapters()` derives each chapter's lines, status, place in each
    recording and on the timeline, and open issues on request.
  - Which audio belongs to which chapter comes from the alignment. The user
    never assigns files to chapters, and a file can hold several chapters.
  - Picking a chapter narrows every step to it: lines and issues by sentence;
    cuts and off-script speech by time in the chapter's part of each
    recording; takes by line; the timeline opens on the chapter.
  - Export and the corrections guide take a `chapter` parameter. A chapter's
    file keeps its number in the book (`02_Chapter_Two…`), so files exported
    one chapter at a time sort together.
  - Analysis still runs on the whole book. Transcripts are cached per file, so
    re-checking after adding one chapter's audio is quick.
  - The edit history (EDL) is the book's: undo on a chapter can undo an edit
    made in another.

## D-060 · One step at a time, in plainer words (2026-10-21)
The user found the single long page hard to follow: unclear what to do next,
too technical, too dense, and slow to act on issues.
- **Steps.** A sidebar lists the workflow in order: Overview, Recordings,
  Manuscript, Check the reading, Fix mistakes, Clean up, Choose takes, Edit,
  Export. Only the chosen step is on screen, with its purpose in one sentence
  and a Next button. Each step shows its state in the sidebar (done, to do, a
  count to review, waiting on an earlier step). It's worked out in
  `state/steps.ts` from data the engine already has.
- **Overview.** Says the one thing to do next, lists every step's state, and
  lists the chapters to pick from.
- **Remembered.** The step and chapter are kept per project in the browser's
  local storage, so a reload comes back to the same place.
- **Words.** Engine terms gave way to plain ones in the UI: "Compare to Text" →
  "Check the reading", resolved/dismissed → "Fixed"/"OK as is", removed/restored
  → "cut"/"kept", "not read" → "skipped". The engine's names are unchanged.
- **Issues.** Each issue is a card: a large play button, what's wrong, and its
  own Fixed / OK as is buttons. Bulk actions appear only once issues are ticked.
  Keyboard shortcuts are unchanged and listed under a disclosure.
- **Undo.** ⌘/Ctrl+Z worked from anywhere only because the Removed panel was
  always on the page. It's now registered by the project page for every step
  (`state/undo.ts`).
- **Look.** Cards, a sticky top bar with the chapter picker, chips for status,
  and stat rows for counts, in light and dark.

## D-061 · Checking the edit for lost lines (2026-10-21)
The user wanted to re-run checks from the Edit step, to catch anything removed by
accident in the earlier steps.
- **What's checked.** Every line that was read in full: is it still heard on the
  timeline as it is now?
  - Words are the transcript's words that the reading heard, in order, each
    measured only within the reading's span. The span is snapped to the audio,
    while the recognizer's word times stray past it.
  - Extras (a stutter's repeat) don't count. If a stutter cut takes one copy of
    a word and the other plays, the word is heard.
  - A word plays if 75% of it is on the timeline. Within the span, what's cut of
    a word is speech, so a quarter gone is audible.
  - A line is fine if one complete reading plays whole. Otherwise its best
    reading is reported: "removed" if less than half plays, else "clipped". The
    report lists the missing words and what cut them (cleanup kind, a take
    choice, a cut of the user's, a mute).
- **Where.** `analysis/edit_check.py` (pure) and `GET /api/project/edit-check`.
  It's computed on request (0.14 s for a 3-chapter book), so the Edit step's list
  updates after every edit. **Re-run checks** there also re-runs the flub and
  pause checks, so Fix mistakes catches up.
- On the user's real project it found two last words clipped by take cuts that
  started 0.2 s early. Lines never read in full stay the Issues list's business.

## D-062 · Pickups (2026-10-21)
The user asked to import re-recorded lines and have them placed over the flubs
automatically, with a prompt where there's more than one take to choose from.
- **A recording has a role:** narration or pickup (schema v8). A pickup isn't
  laid out on the timeline. Only takes placed from it play, so slates, chatter
  and unused attempts in the file never reach the book.
  `default_segments` skips pickups, and `place(..., homeless=True)` takes a
  pickup's piece off the timeline instead of sending it "home".
- **Placement** reuses takes (D-044/D-045):
  - A pickup reading of a line with narration readings joins its take group,
    and the ranking decides (complete, then text errors). A clean pickup over a
    flub wins confidently and goes right before the line's original reading,
    which is cut.
  - A line read only in pickups (skipped in the narration) is a group of one,
    placed before the next line read in the narration. Before the start of that
    recording if nothing precedes it there, so its room-tone lead-in stays with
    what follows.
  - On a re-run, new pickup takes are re-ranked with the old. If the app had
    chosen, the best take wins. If the user had chosen, the choice stays and the
    line goes back to "listen and choose".
- **The checks follow the timeline:** a reading counts as heard only if it's
  laid out, not cut and not muted, rather than "not covered by a removed
  region". The long-pause check skips pickup files. Cleanup in a pickup file
  only looks for stutters.
- A pause is named by the lines read before and after its start, heard or not.
  A pickup replacing the next line used to make the same pause a new issue
  (regression test).
- **UI:** a Pickups card on Fix mistakes. Adding files imports them as pickups,
  and when the import finishes, the reading is checked automatically. Each file
  lists its lines: in place of the flub, fills a skipped line, not used, listen
  and choose, or matches no line. Lines to choose get the take chooser in place.
  Pickups are tagged in Recordings.

## D-063 · Cuts you can see and adjust on the timeline (2026-10-21)
The user couldn't tell how to cut or roll, and couldn't see the cuts.
- A rolling edit needed the playhead exactly on a join sample, which can't be
  hit by clicking. Rolling now acts on a chosen cut (by region, the API Clean up
  uses), or the nearest cut within 1 s.
- Each cut is a ✂ tab in the lane under the narration, with a line through the
  waveform. Clicking it, or C / Shift+C (and Previous/Next cut buttons), chooses
  a cut and opens a panel:
  - listen across the cut, or hear what was cut;
  - move the cut earlier or later (the rolling edit, explained in place);
  - move its start or end ("cut more" / "keep more");
  - restore it.
  Take cuts point to Choose takes.
- A one-line how-to sits above the timeline. Dragging a selection shows a bar
  with Play selection, Cut selection and Clear.
