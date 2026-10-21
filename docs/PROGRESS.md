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

## M5 · Cleanup + EDL: done (2026-10-21)

Acceptance criteria (SPEC §9):
- [x] **Stutter, false-start and commentary removal as EDL operations** (D-040).
  - Fixtures with perfect fake ASR: precision/recall 1.0 for each kind.
  - Noisy fake ASR (5-10%, 10 seeds): no line that must stay is ever cut. Recall is 1.0 for false starts and commentary and 0.8 for stutters: a stutter is missed when the word after it is misheard.
  - **Real small.en** (`test_real_model.py`): exactly the 4 injected cuts, each within 10 ms of the ground truth, all auto-applied.
  - Low-confidence candidates are *suggested*, not applied. The threshold is configurable.
- [x] **Removed Material panel.**
  - Lists kind, where, what was cut, length, confidence and status.
  - Play the join as edited (rendered from the sources) or play what was cut.
  - Restore, re-apply and apply suggestions.
  - Keyboard for every action: j/k, Space, p, r, a, `[` `]` trim 50 ms, `,` `.` roll 50 ms.
- [x] **Undo/redo.** Unlimited and persisted in the project (D-039), via ⌘/Ctrl+Z and Shift+⌘/Ctrl+Z or the buttons, with an edit history. A new edit after an undo drops the redo stack.
- [x] **Rolling-edit restore verified by test.**
  - Dragging the clip edge outward re-exposes the cut audio, sample-identical to the source.
  - A rolling edit moves the cut without changing the timeline's length.
- [x] **Round trip** (SPEC §11): auto-edit, then undo all, renders sample-identical to the sources; redo renders the edit again exactly.
- [x] The checks now see the edited timeline (SPEC §6.7). "Shorten pause" on a long-pause issue cuts its middle, and the next check auto-resolves it; undoing it makes the issue regress.
- [x] Immutability test covers cleanup, edits, undo/redo and renders.

`make check`: 235 engine tests (1 opt-in skipped), 35 web tests. `make e2e`: 5 tests.

Bugs found and fixed, with regression tests:
- **Noise made a second complete take look like a false start.** A misheard final word lowered its coverage, and the whole take was cut. False starts are now fragments only (coverage ≤ 0.6).
- **The misheard end of a line was cut as commentary.** It came out of alignment as "unaligned". Commentary must now be set off from the line before by a pause.
- **MP3 reads from the start:** `-ss 0` changed how ffmpeg skips the encoder delay, so reads from 0 no longer pass it.

Deferred:
- Clip moves, gain, fades and multiple tracks (M7; D-038 notes the planned migration).
- Choosing takes (M6). Takes that are near-complete readings are never cut by cleanup.
- A waveform view of the edited timeline and dragging edges with the mouse (M7). Trims and rolls work from the keyboard now.
- Issues marked as hidden by cuts: a re-run is needed to update the list; the UI says so.

Risks:
- The region model fits one narration track; M7 needs explicit clips (D-038).
- Joins are 10 ms crossfades at speech onsets. They are click-free on the fixtures, but real room tone may make the joins audible; adjustable later.
- Commentary confidence relies on English keyword hints. Long hint-free passages are only suggested (they may be text missing from the manuscript).
- Undo of a whole cleanup run is one step. Undoing it also undoes every cut it made; individual restores are separate steps.

## M6 · Takes: done (2026-10-21)

Acceptance criteria (SPEC §9):
- [x] **Take groups detected.**
  - Fixtures, perfect fake ASR: precision/recall 1.0. The groups are the misread line with its pickup in another file, and a line read twice in one file.
  - Suggestions: the clean pickup beats the misread take, confidently ("fewer text errors"). Two clean reads fall to the later take, marked for review.
  - Loudness breaks ties (a take 12 dB quieter loses).
  - Noisy fake ASR: no invented groups in any seed, and both groups are found in 10/10. When the pickup has fewer real errors it always wins; overall it wins in 7/10 seeds at 5% noise and 3/10 at 10%, because random noise words count as real errors.
  - **Real small.en:** exactly the two groups, and the pickup is chosen confidently.
- [x] **Comparison UI** (Takes panel, SPEC §6.5): the manuscript line; each take with recording, time, score, loudness, transcript, differences, waveform and status; play; A/B switching that keeps your place; loop.
  - Keyboard: 1-9 choose, Space play, ←/→ switch take, L loop, N / Shift+N next/previous line, C confirm, X reject, B back.
- [x] **Preferred take updates the EDL** (D-045).
  - Other takes are cut, and a pickup from another recording is moved to where its line belongs (ordered segments, D-043).
  - The previous choice is one click (or B) away, and every choice is undoable.
  - Flubs are judged on the preferred take: choosing the misread take makes it a flub on the next check.
- [x] Round trip with takes: choose, then undo all, renders sample-identical to the sources.
- [x] Re-running keeps choices. A v5 project migrates with its regions intact.
- [x] Immutability test covers take choices across recordings.

`make check`: 252 engine tests (1 opt-in skipped), 39 web tests. `make e2e`: 6 tests.

Fixed along the way, with regression tests:
- One detection run applying several groups computed each group against stale state, so ids and the segment order would collide. Changes are now written as they are computed, and logged as one entry.
- The Removed Material panel listed take cuts, and restoring one would make a line play twice. Take cuts are now counted there and chosen in the Takes panel.

Deferred:
- Groups spanning several sentences. Per-sentence groups merge adjacent moved pieces, so a re-read paragraph still plays continuously.
- Synced playback of two takes at once, and loop-a-segment within a take. Looping plays the whole take.
- "Organize and Label Files" (SPEC §6.3.2). Pickup placement covers the ordering that matters for takes.

Risks:
- Joins between recordings have no crossfade, and room tone may differ.
- The "later take" tie-break is a convention, not a measurement. Such groups are flagged "listen and confirm".
- A pickup's own leading room tone stays at its file's home position, so a short silence may sit between files.

## M7 · Timeline editor: done (2026-10-21)

Acceptance criteria (SPEC §9):
- [x] **Multitrack** (D-046).
  - The narration track, plus any number of tracks (alternates, room tone, notes) with free clips.
  - Per-track gain, pan, mute and solo; add, rename and delete tracks.
  - Clips move between tracks: off the narration it closes up, and back on it re-inserts.
- [x] **Trim/split/move/fade/gain**, each one undoable entry:
  - split at the playhead;
  - ripple delete of a selection;
  - trim (ripple), where dragging outward re-exposes cut audio across adjacent cuts;
  - rolling edit;
  - move: a ripple insert within the narration, free on other tracks, or across tracks;
  - linear fade in/out, clip gain and clip mute.
  - All tested sample-exact, including the round trip: after every kind of edit, undo-all renders the sources exactly.
- [x] **Snapping:** to words, sentences, pauses (edges and middle), markers, issues and clip edges. Alt+←/→ jumps, and drags snap within 8 px. Words, sentences and pauses come from windowed lanes mapped to timeline time.
- [x] **Markers:** user markers anchored to the recording, so they follow edits (and disappear if their audio is cut). Open issues are coloured markers by kind. Removed material is a ghosted lane that can be restored from it (Shift+R).
- [x] **Realtime preview from the EDL** (D-048): Web Audio schedules the placed clips from source excerpts, with clip and track gain, fades and the server's pan law. Edits apply on the next play, with no rendering. "Render preview" plays the exact server mix.
- [x] **UI:**
  - Canvas lanes: ruler, markers, tracks with waveforms from the multi-resolution peaks chosen by zoom, removed material, transcript.
  - Zoom and scroll; track headers; a clip inspector.
  - Mouse: drag to move, drag edges to trim, drag the top corners for fades, drag the ruler to select.
  - **A key for every tool** (listed in the panel). Edits are queued, so quick repeats build on each other.
- [x] Immutability test covers the timeline tools, a second track and a stereo render.

`make check`: 278 engine tests (1 opt-in skipped), 49 web tests. `make e2e`: 7 tests.

Bugs found and fixed, with regression tests:
- **A split was crossfaded with itself.** The change was tiny (about 1e-9) but not exact; contiguous audio is no longer blended.
- **Mono lost 3 dB in a stereo mix.** That's ffmpeg's upmix; mono is now read as mono and copied to both sides.
- **An outward trim stopped at the first of two adjacent cuts.** It now follows the chain.
- **Preview chunks that began before the playhead started at the wrong offset.**
- **Two quick "+" presses gave +1 dB.** Each press read the state before the last edit returned.
- **The timeline view mapped issues and cuts by linear scan.** It now uses binary search: 3,000 lookups on a 10 h timeline in well under 0.5 s.

Deferred:
- **Live re-scheduling while playing.** An edit is heard from the next Play; seeking during playback restarts it.
- **The 10 ms join crossfades in the browser preview.** The server render has them.
- **Free clips rippling with narration edits.** They stay put, as in Audacity.
- **A visual history panel beyond the edit list** in Removed Material.

Risks:
- The canvas redraws the visible window on every change; very long zoomed-out views of many clips may need tiling.
- Preview fetches excerpts from the engine (localhost) about 8 s ahead. On slow disks the first chunk may start late; it joins in at the right offset rather than drifting.

## M8 · Finalize + export: done (2026-10-21)

Acceptance criteria (SPEC §9):
- [x] **Noise reduction:** spectral gating against a room-tone profile (selected, or the quietest second found automatically), with adjustable strength (D-050). On a synthetic test the noise floor drops by more than 8 dB with speech RMS within 1 dB.
  - On the fixtures it is what makes ACX pass: normalizing them from -25.5 dB to -20 dB RMS would lift the -65 dB noise floor past -60. The report flags this when noise reduction is off (tested).
- [x] **Auto-level:**
  - clip-level gain riding toward the narration's median level (±6 dB max), so takes and chapters match;
  - a gentle soft-knee compressor;
  - an optional gate/expander;
  - normalization to the target RMS;
  - a look-ahead limiter that never exceeds its ceiling.
  - The chain is project settings, applied at render time (nothing baked in), with an A/B preview of any stretch.
- [x] **Loudness presets with a pass/fail report.**
  - The ACX/Audible preset is dated 2026-10, confirmed by the user (open question 10), and holds RMS -23…-18 dB, peak ≤ -3 dB, noise floor ≤ -60 dB RMS, 192 kbps+ CBR MP3 at 44.1 kHz, 1–5 s room tone at each end, and ≤ 120 min per file. Custom presets can be added.
  - The report covers peak, RMS, noise floor, digital silence, length, bitrate and sample rate per file, measured on the file *as written* (decoded back, D-052).
- [x] **WAV/MP3 export.**
  - WAV 16-bit (TPDF dither) or 24-bit; MP3 CBR via LAME, encoded once from the lossless render.
  - Single file, per chapter (found from the manuscript alignment) or per marker section, with room tone at the head and tail.
  - Pre-flight lists unresolved issues, lossy or missing sources, files and lengths, and the last levels check.
  - MP3 from MP3 recordings is refused (lossy to lossy).
  - The fixture book passes ACX as MP3 (44.1 kHz, 192 kbps, mono) and as 24-bit WAV per chapter. With real small.en, the chapters are found and the book passes ACX.
- [x] **Narrator guide:** PDF, CSV and Markdown, with page, time, recording, type, expected, heard and notes (D-053).
  - Sorted by page or time. Times refer to the original recordings or the edited master, as chosen and stated in the guide.
  - Header with project, narrator and date, plus counts by type.
- [x] Immutability: finalize, exports and the guide leave the sources unchanged, and nothing is written beside a referenced recording.

`make check`: 293 engine tests (1 opt-in skipped), 54 web tests. `make e2e`: 8 tests.

Bugs found and fixed, with regression tests:
- Limiter blocks that didn't start on its 1 ms frame grid differed from one pass; blocks are now aligned (`block_quantum`).
- Export file names doubled and kept underscores from punctuation.

Deferred:
- LUFS and true-peak measurement. ACX uses RMS and sample peak; LUFS needs K-weighting filters (adding it would mean scipy or our own biquads).
- Picking the room-tone region on the timeline (the API takes it; the UI shows the automatic choice).
- Opening/closing credits and retail sample files (ACX asks for them; they're regular exports of chosen ranges).
- Background export resumption after a crash: an interrupted export leaves no partial folder, and is simply run again.

Risks:
- The DSP is deliberately simple. Noise reduction can sound watery on hard noise; strength and an A/B preview are the guard.
- The ACX numbers are unverified at the source (open question 10).
- Long single-file exports are limited by temp disk (pass 1 writes float32: about 600 MB per hour at 44.1 kHz mono).

## M9 · macOS packaging: done (2026-10-21)

Acceptance criteria (SPEC §9):
- [x] **Signed-ready `.app` wrapper** (D-054, D-057; how-to in `docs/PACKAGING.md`).
  - The shell is Tauri 2 (`shell/src-tauri`). It shows a splash, starts the bundled PyInstaller engine (`Contents/Resources/engine/`, onedir, about a 1 s start), and opens its loopback URL. It stops the engine on quit, and the engine also exits if the shell dies (stdin closes).
  - Hardened runtime with entitlements and no sandbox. Signing and notarization run when the `APPLE_*` variables are set; otherwise the app is ad-hoc signed. Minimum macOS 12.
  - `scripts/build_macos.sh` builds the `.app` and `.dmg` with bundled static ffmpeg/ffprobe. It then runs the app's `--smoke-test` and a real first run on the engine inside the app.
  - The CI job `macos-app` (macos-14, Apple Silicon) runs this and uploads the `.dmg`.
  - Proven here on Linux:
    - the frozen engine built with the same spec passes `scripts/smoke_engine.py --bundled-ffmpeg`: a real tiny.en download from Hugging Face, import, transcription and comparison, chapter MP3 export passing ACX, and the guide PDF, all with the bundled ffmpeg;
    - the Tauri shell builds (webkit2gtk) and its `--smoke-test` starts the frozen engine and gets health and the UI page.
- [x] **First-run model download** (D-055).
  - A pinned catalog (tiny/base/small/medium.en at fixed revisions, with every file's size and hash).
  - An explicit download job with progress, cancel and HTTP Range resume, checksum verification and an install marker. Nothing downloads implicitly, and transcription refuses a missing model with its size.
  - UI: a first-run banner, and a Speech models list with download/remove. Analysis is disabled until the chosen model is installed.
  - Tested against a fake Hugging Face server (resume, truncated transfer, bad checksum, cancel, API), and once against the real one (opt-in test).
- [x] **Web mode still works.** `make dev` is unchanged. `hillaudio serve` / `make serve` serves the built UI and the API on one port and prints a READY line. It refuses non-loopback hosts and exits with stdin; tested end to end in a subprocess.
- [x] Immutability, local-only: unchanged. The model download is the only network call; the user starts it, and it sends nothing of theirs.

`make check`: 305 engine tests (2 opt-in skipped), 57 web tests. `cargo test -p hillaudio-launcher`: 4 tests. `make e2e`: 9 tests.

Bugs found and fixed, with regression tests:
- A truncated model download failed its checksum and was deleted. It is now kept as `.part` and resumes.
- `/api/asr` sizes disagreed with the catalog. They now come from the catalog.
- **Undo/redo shortcut race.** Shift+Ctrl+Z right after an undo was sometimes ignored: the window key listener was re-registered in a passive effect, after the "Redo" button had already rendered. It now uses a layout effect. Covered by the RemovedPanel undo/redo test, which caught it under load.

Deferred and risks:
- **The `.app` runs only in CI.** It can't be launched in this container. The CI smoke tests are the proof, and a manual check on a real Mac (opening the window, Gatekeeper with a real Developer ID) is still worth doing.
- **Bundled ffmpeg.** Our own LGPL build, from verified sources. The first CI run had depended on a third-party binary host, which was down (HTTP 521). Its source is published with each release (open question 11, answered).
- **Intel Macs** aren't supported (open question 5, answered: Apple Silicon only).
- **Engine size.** The engine is about 0.6 GB (ctranslate2, onnxruntime, PyAV). The `.dmg` compresses it; trimming unused libraries is possible later.
- **FFmpeg on macOS.** ffmpeg.org is blocked from this container. The build script was tested here end to end with Ubuntu's copy of the signed 6.1.1 release tarball (signature check, LGPL configuration, the engine tests, a tampered tarball rejected); CI builds 8.0 on macOS.

## After M9 · answers to the open questions (2026-10-21)
- Questions 5, 9, 10 and 11 answered (D-058):
  - Apple Silicon only.
  - Lossy recordings default the export to WAV (tested).
  - The ACX numbers are confirmed, and the preset's caveat is gone.
  - The ffmpeg source is kept by every build and uploaded by CI as `ffmpeg-source`, for each release.
- **Bug found and fixed**, with a regression test: Alt+Up/Down right after "add track" could be ignored. It read the tracks from the last render, which didn't have the new one yet. The move is now built from the queued view, like the other edits. Found as an intermittent m7 e2e failure.

## Open questions for the user (SPEC §13 and found in M0)
Defaults are assumed until answered; none blocks M1.
1. Typical project size (hours, number of takes)? *Assumed: SPEC §8's 10 h / ~2 GB as the upper bound.*
2. Primary export target ACX/Audible or general? *Assumed: ACX preset as default, custom presets allowed.*
3. Most common manuscript formats? *Assumed: SPEC §9 order (.txt, .docx, .pdf).*
4. Guide timestamps reference original recordings, edited master, or both? *Assumed: selectable, default original recording.*
5. ~~Minimum macOS version; Apple Silicon only or Intel too?~~ *Answered 2026-10-21: Apple Silicon only, no Intel build (D-058). The minimum stays macOS 12.*
6. MIDI / music-bed track ever needed? *Assumed: no (SPEC §2 non-goal).*
7. ~~pedalboard is GPL-3.0.~~ *Resolved in M8 without needing a decision: the DSP is our own numpy code (D-050).*
9. ~~MP3 recordings and ACX.~~ *Answered 2026-10-21: export as WAV. A book with lossy recordings now defaults to WAV export, with the reason shown; MP3 stays refused (D-058).*
10. ~~Confirm the ACX numbers.~~ *Answered 2026-10-21: RMS -23 to -18 dB, peaks at or below -3 dB (or -3 dBTP), noise floor at most -60 dB RMS. These match the preset; its "verify" caveat is gone (D-058).*
11. ~~Distribution of the bundled ffmpeg.~~ *Answered 2026-10-21: yes, publish the source tarballs and the build script with each release. The build keeps them, and CI uploads them as the `ffmpeg-source` artifact (D-058).*
8. **Commercial use?** If the app will ever be sold or used commercially, CrisperWhisper's weights are off the table, which D-007 already assumes.
