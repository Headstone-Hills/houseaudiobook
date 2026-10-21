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

## M2 · Text import & page mapping: done (2026-10-20)

Acceptance criteria (SPEC §9):
- [x] **`.txt/.docx/.pdf` import** (plus `.md`), by path or upload, with a live preview of the page mapping before anything is written. The original is copied read-only into `text/` and re-mapping uses the copy. Clear errors for scanned PDFs, password-protected PDFs, EPUB, .doc, RTF and ODT. Tests: `test_text.py`, `test_manuscript_api.py`, `ManuscriptPanel.test.tsx`, `e2e/m2-manuscript.spec.ts`.
- [x] **Page offset**: "printed page 1 is physical page N" for counted pages; PDF page labels used when present (or ignored on request); front-matter skip.
- [x] **Manuscript units with pages**: chapters, paragraphs and sentences, with char offsets, start/end page, printed number, label, and estimated/front-matter flags (schema v2).
- [x] **Unit tests on page edge cases** (`test_text.py`, 34 tests):
  - markers between paragraphs, mid-paragraph and mid-sentence (start/end pages);
  - text before the first marker;
  - consecutive and backwards markers (warning);
  - custom marker regex and validation;
  - form feeds, including blank pages, a trailing feed and a feed mid-line;
  - offset and front matter;
  - estimate boundaries, labelled "approx.";
  - .docx breaks inside runs, `pageBreakBefore` (incl. switched off), and section breaks (nextPage vs continuous);
  - localized heading styles, tracked changes and field codes;
  - PDF running heads, page numbers, hyphenation, paragraphs across pages, roman front-matter labels, blank and scanned pages, and a title page matching the running head;
  - sentence splitting: abbreviations, initials, quotes, ellipses, decimals.
- [x] **Golden** (`test_manuscript_golden.py`): the committed `.txt`, `.docx` and `.pdf` fixtures (generated by `make fixtures`) all yield the ground truth's 13 sentences on the same pages, and the same 2 chapters.
- [x] **Immutability**: preview, import, re-map, upload and reopen-and-verify leave manuscript originals byte- and mtime-identical. Checked by planting an `os.utime`; the test failed as it should.

`make check`: 125 engine tests, 20 web tests. `make e2e`: 2 tests, stable across 4 consecutive runs.

Bugs found and fixed, with regression tests: a form feed mid-line glued words together; estimate-mode front-matter skip missed plain-text chapter headings; one-line PDF pages lost paragraph breaks; front-matter skip dropped a whole paragraph that ran onto page 1; a PDF title page matching the running head was stripped; the UI jumped into the engine's open project on an old job update (D-027).

Deferred:
- EPUB (SPEC §6.2), with chapter-relative positions or estimate mode. Not in M2's acceptance criteria.
- Scanned PDFs (OCR), out of scope for v1 per SPEC.
- Footnotes and endnotes in .docx (ignored as not narrated). Revisit if narrators read them.

Risks:
- PDF paragraph detection is heuristic (D-025). Unusual layouts (two columns, drop caps, poetry) may split or merge paragraphs. Sentences and pages stay correct, because pages come from the PDF itself.
- Re-mapping replaces unit ids (D-023); M4's stable keys must not depend on them.
- .docx without explicit breaks falls back to an estimate, even if the author sees real pages in Word (D-024).

## M3 · Transcribe + Compare to Text: done (2026-10-20)

Acceptance criteria (SPEC §9):
- [x] **Word-level transcripts**: faster-whisper small.en with a disfluency prompt (D-028). Words have timestamps and confidence; transcripts are cached by file hash + settings; jobs are cancellable. Timestamps are snapped to the audio (raw p95 500 ms, snapped 10-12 ms).
- [x] **Alignment with per-unit scores**: each manuscript sentence gets readings (takes) with coverage, score and classified differences (D-029, D-030). False starts, stutters, off-script speech, retakes and pickups are each represented explicitly.
- [x] **Report on fixtures matches injected errors above target accuracy**:
  - Perfect (fake) ASR: precision 1.0, recall 1.0 on the injected misread and skip, and every other injected event is reproduced exactly (`test_align.py`).
  - Noisy ASR, 5-20% wrong words plus dropped stutters, over 20 seeds: at least 99% of sentences found at ≤10% noise and at least 95% at 20%; recall 1.0 on injected errors.
  - **Real small.en** through the app pipeline (`test_real_model.py`, opt-in): exactly the injected misread and skip flagged, both stutters found, the commentary unaligned. Benchmark: 15/15 sentences found, precision 1.00, recall 1.00 (`docs/bench/asr-2026-10-20.md`).
- [x] Spike 01 closed with measurements (D-028); D-007 and D-008 confirmed.
- [x] UI: Compare to Text panel. Model choice with its download size; run; results filterable (with differences / not read / clean / all); each sentence's takes with times, scores and differences; off-script speech.
- [x] Immutability test covers Transcribe + Compare to Text.

`make check`: 178 engine tests (1 opt-in skipped), 23 web tests. `make e2e`: 3 tests.

Bugs found and fixed, with regression tests:
- **Benchmark measurement:** stutter survival counted the sentence's own word; a "-" token was counted as a word; a stutter word was taken as the sentence start.
- **Boundary snapping:** quiet endings were clipped; a start was snapped into the previous (stutter) word.
- **Alignment:** a misread final word fell into the next unaligned stretch; false starts were padded with "missing" words; a retake's first word was given to the next sentence; a DP tie paired words across a skipped sentence.
- **UI:** an unnamed list; HTML lists inside `<summary>`.

Deferred to M4 (Issues):
- Turning differences into issues with severity, stable keys and decisions.
- Lowering severity for phonetically close substitutions. Every false flag in the benchmark was a near-homophone, e.g. "trellis"/"trelleys".
- Jump-to-audio playback.
- Overlong silences.

Risks:
- The model decision rests on a synthetic voice and 13 sentences. Re-check on a real narration recording before M8.
- Long restarts appear as partial readings and short ones as "extra" differences (D-030); M5 must handle both.
- Re-mapping pages clears comparison results (by design, D-031); the panel then says the results are out of date and asks for a re-run (transcripts are reused).

## M4 · Issues pipeline: done (2026-10-21)

Acceptance criteria (SPEC §9):
- [x] **Flubs and overlong silences flagged with timestamp + page.**
  - A sentence with no clean complete take is a *flub*, pointing at its best take; a sentence never read is an *unread line* (D-033).
  - Pauses over the threshold for their boundary are *long pauses*: 2.0 s within a paragraph, 3.0 s at a paragraph break, 5.0 s at a chapter break, all configurable (D-035).
  - Each issue carries its recording, time range and page.
  - Fixtures, perfect fake ASR: flub precision/recall 1.0 (only the skip; the misread has a clean pickup, and stutters are repeats, not flubs). Silence precision/recall 1.0, with both boundary types right.
  - Noisy fake ASR: the skip is found in every seed.
  - **Real small.en** (`test_real_model.py`): exactly the skip and the two pauses.
- [x] **Issues panel.**
  - Sortable (page, recording time, severity, type) and filterable (type, status, severity).
  - Expected vs heard, change badges, notes, bulk actions.
  - Keyboard shortcuts for every review action: ↑/↓ or j/k, Space, r, d, o, n, x.
- [x] **Jump-to-audio.** Space or the time link plays a WAV excerpt with ±3 s of context, decoded from the original source (D-037).
- [x] **Re-run with diff and preserved decisions.**
  - Stable keys come from the kind and the sentence's text in context (D-034).
  - Re-runs mark issues *new*, *still open*, *fixed* (auto-resolved) or *back again* (regressed).
  - Dismissed and hand-resolved issues stay as the user left them, also across a page re-map.
  - Every status change a re-run makes is logged `auto` with its previous status. Every user decision is logged `user` (D-036).
- [x] Immutability test covers checks, decisions and excerpts.

`make check`: 206 engine tests (1 opt-in skipped), 30 web tests. `make e2e`: 4 tests.

Bugs found and fixed, with regression tests:
- A medium.en mishearing ("laughed" → "left") was high severity because the spellings differ. It is now caught by a sound-alike key.
- The `make check` mypy error on an `int | None` sort key in a test.

Deferred:
- "Shorten to N seconds" for pauses. It needs the EDL (M5). Each pause already carries its `suggested_s`.
- Re-run *scope* by selected range (SPEC §6.7). Stage scope works ("Re-run checks" runs flubs + silences).
- Re-analyzing the *edited* timeline. Until M5 there are no edits, so checks run on the sources.
- Issue markers on the waveform (M7 timeline).
- An undo button for auto changes. They are logged with the previous status and can be reopened by hand. Full undo/redo arrives with the EDL history in M5.

Risks:
- Silence detection uses the energy speech mask. Noisy rooms or breaths can split or merge pauses. Thresholds are adjustable, but the mask itself is not yet.
- The severity heuristic could rank a real misread of a similar-sounding word as "Check". Those issues are still listed, only ranked lower.
- Stable keys depend on sentence text. Editing a sentence's wording in the manuscript makes its issues new (old decisions stay on the old keys, unmatched).
- Fixtures are synthetic (one voice, 13 sentences). Re-check on a real narration before M8.

## M5 · Cleanup + EDL: in progress

Plan:
1. **`analysis/cleanup.py`** (pure): cut candidates with a reason, confidence and stable key.
   - **Stutters:** extra words tagged `repeat` by the alignment. Cut from the repeat's onset to the next word's onset.
   - **False starts:** a partial reading followed soon after by a reading of the same sentence. Cut from the partial's onset to the restart's onset.
   - **Commentary:** unaligned speech. Cut from its onset to the next reading's onset. Keyword hints ("sorry", "let me", "again", "hold on"...) raise confidence; long hint-free passages stay *suggested*, since they may be text missing from the manuscript.
   - Without a manuscript: immediate word repeats from the transcript alone.
   - Edges snap to speech onsets (the energy mask). Confidence ≥ threshold (0.7, configurable) is applied; lower is *suggested*.
2. **EDL model** (`edl/`, pure). The narration track is the sources in order, minus *removed regions*: sample-accurate integer ranges per source, each with a kind, reason, origin, status and stable key. Clips and timeline positions are derived from them, so every edit ripples by construction.
   - Rolling/ripple edits on a clip edge move a region's edges. Dragging an edge outward re-exposes removed audio.
3. **Store and undo.** Schema v5 `edit_region`. Every edit is one operation-log entry with before/after rows, origin `auto` or `user`.
   - Undo/redo is unlimited and persisted (`reverted_at`); a new edit after undo clears the redo stack.
   - Re-running cleanup keeps user decisions (stable keys).
4. **Pipeline and checks.** A `cleanup` stage (before the checks).
   - Checks see the current timeline: readings that were cut are ignored, and pauses are measured with cut audio removed.
   - "Shorten pause" on a long-pause issue cuts its middle down to N s. The next check auto-resolves it.
5. **Render.** A sample-exact render of the timeline (or a range), decoded from the original sources, used for previews and the round-trip test.
6. **API and UI.**
   - Removed Material panel: kind, text, time, confidence, status; restore, re-apply, play the cut, play the join; keyboard shortcuts.
   - Undo/redo (Ctrl/⌘+Z, Shift+Ctrl/⌘+Z) and a history list.
   - Shorten from the Issues panel.
7. **Tests.**
   - Precision/recall for stutter, false-start and commentary cuts on the fixtures (perfect and noisy fake ASR, plus real small.en).
   - Round trip: auto-edit, then undo all, renders sample-identical to the sources.
   - Rolling-edit restore; undo/redo across reopening; immutability; API, UI and e2e.
- **Risks.**
  - The region model fits one narration track. M7 multitrack moves/gain/fades will need explicit clips (planned migration).
  - Cutting at speech onsets without crossfades may click on real recordings; render adds short fades at joins only.

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
