# HillAudiobook — Product & Engineering Spec

> Status: v0.1 draft. Read `CLAUDE.md` first for working rules. This file is the source of truth for *what* to build; `CLAUDE.md` is the source of truth for *how to work*.

## 1. Summary

HillAudiobook is a first-pass audiobook editing tool. It ingests multiple narration recordings plus the source text, transcribes the audio, aligns it to the text, and produces:

1. A cleaned, organized first-pass edit (stutters, false starts, and off-script commentary removed).
2. A reviewable **action list** of problems (flubbed or incorrect lines, overlong silences), each with a **timestamp** and a **page number**.
3. A **take-comparison** interface for lines the narrator recorded more than once.
4. A **multitrack timeline editor** (Audacity-style) for final cleanup.
5. An optional **finalizing** pass (normalize, auto-level, noise reduction).
6. Exports: audio (`.mp3`, `.wav`) and a **narrator corrections guide**.

**Platforms:** native macOS app, plus a browser-based web app option. Both are the same codebase (see §3).

**Core principle: nothing is ever destructive.** Original audio files are never modified. Every edit, automated or manual, is a reversible entry in an edit decision list (EDL).

## 2. Goals and non-goals

**Goals**
- A user can go from raw recordings and a manuscript to an exportable first-pass edit plus corrections guide with minimal manual work.
- Every automated decision is visible, explainable, and reversible.
- Checks can be re-run at any time without losing the user's decisions.

**Non-goals (v1)**
- Not a full DAW: no MIDI, no plugin hosting, no multi-user collaboration.
- No cloud processing: all audio and text stay on the user's machine.
- No narration synthesis or voice cloning.
- No formats beyond `.mp3` and `.wav` for import/export (architecture should make adding more easy).

## 3. Architecture

Recommended stack (change only with a note in `docs/DECISIONS.md`):

| Layer | Choice | Why |
|---|---|---|
| Engine | Python 3.11+ | Best ecosystem for ASR, alignment, and audio DSP. |
| API | FastAPI over localhost (HTTP + WebSocket for job progress) | Lets the same engine serve a browser tab or a native shell. |
| Frontend | React + TypeScript + Vite | Needed for a responsive timeline/waveform UI. |
| Project store | SQLite (one `.hillproj` folder per project) | Durable, queryable, crash-safe, easy undo log. |
| Audio I/O | ffmpeg (decode/encode), `soundfile`, `numpy` | Robust `.mp3`/`.wav` handling. |
| Transcription | `faster-whisper` or `mlx-whisper` (Apple Silicon) with word timestamps | Local, accurate; spike both in M1. |
| Alignment | `stable-ts` or WhisperX forced alignment | Tighter word boundaries than raw ASR timestamps. |
| Text diff | `rapidfuzz` + custom word-level sequence alignment | Handles insertions, deletions, substitutions. |
| Loudness | `pyloudnorm` + custom RMS/peak routines | Measurement for mastering targets. |
| DSP | `pedalboard` (compressor, limiter, EQ, gate) + `noisereduce` | Quality offline effects. |
| macOS packaging | Tauri shell with Python engine as sidecar (fallback: PyInstaller + pywebview) | Native `.app`, small footprint. |

**Delivery order:** build and ship as a localhost web app first. The "native macOS" version is the same app wrapped in a shell (M8). This keeps the web option free.

**Engine/UI boundary:** the UI never touches audio files directly. It calls the engine API, plays back through pre-rendered peaks/proxies, and renders the EDL in real time in the browser.

### Suggested repo layout
```
hillaudiobook/
  CLAUDE.md
  SPEC.md
  docs/DECISIONS.md          # running log of architecture decisions
  engine/
    hillaudio/
      ingest/                # file import, hashing, probing, proxies
      text/                  # source text parsing, page mapping, normalization
      asr/                   # transcription + alignment wrappers
      analysis/              # diff, flub detection, silence, takes, cleanup
      edl/                   # edit decision list, operations, undo/redo
      render/                # offline mixdown, finalize chain, export
      guide/                 # narrator guide generation
      api/                   # FastAPI app, job runner
    tests/
    fixtures/                # generated test audio (see §11)
  web/
    src/
      pages/                 # Projects, NewProject, ActionList, Takes, Timeline, Export
      components/
      state/
  shell/                     # Tauri wrapper (M8)
  scripts/
```

## 4. Data model

All project state lives in `<ProjectName>.hillproj/`:
```
project.db            # SQLite
sources/              # imported audio, copied or referenced (hash-verified), NEVER modified
text/                 # original manuscript + normalized text
cache/                # transcripts, alignments, waveform peaks, proxies (all regenerable)
exports/
```

Core tables (names indicative):
- `source_file(id, path, sha256, duration, sample_rate, channels, label, order_index)`
- `transcript_word(id, source_file_id, start, end, text, confidence)`
- `manuscript_unit(id, kind[chapter|paragraph|sentence], text, page, char_start, char_end)`
- `alignment(id, manuscript_unit_id, source_file_id, start, end, match_score, run_id)`
- `take_group(id, manuscript_unit_id)` and `take(id, take_group_id, source_file_id, start, end, score, status[preferred|alternate|rejected])`
- `issue(id, kind, severity, source_file_id, start, end, page, manuscript_unit_id, status[open|resolved|dismissed], stable_key, run_id)`
- `edl_clip(id, track_id, source_file_id, source_in, source_out, timeline_start, gain_db, fade_in, fade_out, muted)`
- `operation_log(id, seq, op_type, payload_json, origin[auto|user], reverted_at)` — powers undo/redo and audit trail
- `analysis_run(id, started_at, options_json, status)`

**Stable issue keys:** each issue gets a deterministic `stable_key` (e.g., hash of kind + manuscript unit + approximate source position). When checks are re-run, issues with matching keys keep their `status` (resolved/dismissed). New problems appear as new; fixed problems auto-close.

## 5. Non-destructive editing model

- Source files are immutable and hash-verified on every project open. If a hash mismatches, warn and offer relinking.
- The timeline is a list of clips referencing `(source_file, source_in, source_out)`. Cuts, trims, moves, gain, and fades change only EDL rows.
- **Rolling edit / restoring lost material:** every clip edge retains access to the full source ("handles"). Dragging an edge outward re-exposes removed audio. Automated removals (stutters, commentary) are stored as *removed regions* that can be restored individually from a "Removed material" panel, or via a ripple/rolling trim on the edge. Rolling edit on adjacent clips moves the shared boundary without changing total length.
- Every operation is logged with `origin` (auto/user). Full unlimited undo/redo, persisted across sessions.
- Offline render happens only on export or "Render preview", never in place.

## 6. Workflow

### 6.1 Project start
1. Launch → **Projects** screen: recent projects, *Open Existing*, *New Project*.
2. **New Project** wizard:
   - Name + location.
   - Function checkboxes (all default on, with dependency handling):
     - Transcribe
     - Compare to Text *(requires Transcribe; requires text import)*
     - Organize and Label Files
     - Clean up stutters / commentary / false starts *(requires Transcribe)*
     - Mark flubbed or incorrect lines *(requires Compare to Text)*
     - Mark overlong silences
     - Compare multiple takes of same line *(requires Compare to Text)*
   - Selecting a function auto-selects and explains its prerequisites.
3. **Import audio** (multi-select, drag-and-drop; `.mp3`, `.wav`). Show duration, sample rate, channels; warn on mixed sample rates (engine resamples internally).
4. **Import source text** (see §6.2).
5. **Run automated functions**, with a progress screen (per-file, per-stage, cancellable, resumable).

### 6.2 Source text and page numbers
Page numbers are a headline feature, so the import must handle where they come from:
- `.txt` / `.md`: user may supply page markers (a configurable pattern such as `[p. 12]`, form feeds) **or** a words-per-page estimate. Clearly label estimated pages as "approx."
- `.docx`: use explicit page breaks where present; otherwise offer estimate mode.
- `.pdf`: native page numbers (text-layer PDFs; scanned PDFs are out of scope for v1 and should produce a clear error).
- `.epub`: no fixed pages — offer chapter-relative positions or estimate mode.
- Allow **page offset** (e.g., front matter makes printed page 1 = PDF page 9) and a front-matter skip.
- Every manuscript unit stores a page. Every issue inherits its page through alignment.

### 6.3 Automated functions (analysis pipeline)
Stages run in order; each is individually re-runnable and writes to `analysis_run`.

1. **Transcribe** — word-level timestamps and confidence per file.
2. **Organize and Label Files** — infer file order and chapter/section by matching transcript content to manuscript position (not filename alone). Auto-name e.g. `Ch03_Take1_p41-58`. User can reorder/rename; labels are metadata only (no file renames on disk).
3. **Compare to Text** — normalize both sides (case, punctuation, numerals/number words, contractions, hyphenation), align at word level, produce per-unit match scores. Classify differences: *missing words*, *extra words*, *substitution*, *transposition*, *reordered*, *mispronunciation suspected* (low ASR confidence on a text-matched word).
4. **Clean up stutters / commentary / false starts**
   - *Stutters/repeats:* immediate repeated words or fragments.
   - *False starts:* partial phrase followed by a restart matching the same manuscript location.
   - *Commentary:* speech that does not align to the manuscript (e.g., "sorry, let me do that again", "okay, from the top"). Keyword hints plus alignment failure; sensitive to context.
   - Action: create removal edits in the EDL (not deletions). Each removal logged with a reason and confidence. Low-confidence candidates go to the action list as *suggested* rather than being auto-applied (threshold configurable).
5. **Mark flubbed/incorrect lines** — manuscript units whose alignment score falls below threshold or contain missing/extra/substituted words. Output: issue with timestamp range + page + expected vs. heard text.
6. **Mark overlong silences** — gaps above a configurable threshold (default suggestion: > 2.0 s; separate thresholds for paragraph and chapter boundaries). Mark only; offer one-click "shorten to N seconds".
7. **Compare multiple takes** — detect when the same manuscript span appears more than once across (or within) files; group into `take_group`. Rank takes by alignment accuracy, clean-ness, and (optionally) loudness consistency. Pre-select a suggested preferred take; user confirms.

### 6.4 Action list ("Issues" panel)
A sortable, filterable table: type, severity, page, timestamp, expected vs. heard, status. Click to jump playhead to the spot with ±3 s of context; keyboard shortcuts for next/previous issue, resolve, dismiss, add note. Bulk actions.

### 6.5 Take comparison view
- One take group per manuscript unit: show manuscript text, each take's transcript with diffs highlighted, and its waveform.
- Instant A/B switching, synced playback, loop segment.
- Mark **Preferred / Alternate / Rejected**. Choosing a preferred take updates the EDL; the previous choice remains one click away.
- Keyboard-first workflow (1–9 selects take, Space plays, N next group).

### 6.6 Multitrack timeline editor
Audacity-inspired, but non-destructive:
- Multiple tracks (e.g., narration, alternates, room tone, notes), mute/solo, per-track gain, pan.
- Waveform display with zoom, selection, markers, labels (issues appear as colored markers).
- Tools: select, trim/rolling edit, split, move, ripple delete, fade handles, clip gain.
- Snap to words, sentences, silences, and markers.
- Transcript lane under the waveform for text-driven navigation.
- "Removed material" lane showing ghosted automated cuts that can be restored.
- Full undo/redo history panel.

### 6.7 Re-run checks
A persistent **Re-run checks** button, available at any point:
- Re-analyzes the *current* timeline (post-edit) against the manuscript.
- Produces a new action list, diffed against the previous run: *new*, *still open*, *auto-resolved*, *regressed*.
- Preserves user decisions via `stable_key` (§4).
- Choice of scope: whole project, selected range, or specific stages.

### 6.8 Finalizing (optional)
A separate **Finalize** step with a preview-able, adjustable chain:
1. Noise reduction (profile from user-selected room-tone region or auto-detected quiet region; adjustable strength; A/B preview).
2. Optional gate/expander for noise between phrases.
3. Auto-level: gentle compression and clip-level gain riding so loudness is consistent across files/chapters.
4. Loudness normalization and peak limiting to a target preset.
5. Report: peak, RMS, noise floor per output file with pass/fail against the preset.

**Presets:** include an **ACX / Audible** preset by default: per ACX's published requirements, each file must measure between −23 dB and −18 dB RMS, peak no higher than −3 dB, and noise floor no higher than −60 dB RMS. Allow custom presets. *Implementer note: confirm current ACX requirements (including MP3 bitrate/sample rate and room-tone lead-in/out) on acx.com before hard-coding; they can change.*

Finalizing is non-destructive: it is a render-time chain on the master bus, stored as settings, not baked into the sources.

### 6.9 Export
- **Audio:** `.wav` (16/24-bit, project sample rate) and `.mp3` (selectable bitrate, CBR option). Options: single file, or split per chapter/section using labels. Always render from lossless sources; never transcode an MP3 twice.
- **Narrator corrections guide:** list of marked corrections with timestamp, page number, expected text, heard text, issue type, and notes. Formats: PDF (for reading), plus CSV and Markdown. Sorted by page (default) or timestamp. Include project/narrator header, date, and a summary count by issue type. Timestamps must refer to a defined reference (e.g., "in the original recording `Ch03_Take1.wav`" vs. "in the edited master") — make this selectable and state it in the guide.
- Export dialog shows a pre-flight summary (unresolved issues, levels report).

## 7. UX principles
- Progressive disclosure: wizard first, power tools after.
- Never lose work: autosave on every operation; crash recovery on relaunch.
- Everything automatic is labeled as such and reversible.
- Long operations are cancellable, resumable, and show real progress.
- Keyboard-first for review tasks (hundreds of issues is normal).
- Accessible: full keyboard nav, sensible contrast, resizable UI.

## 8. Performance and reliability targets
- Handle a 10-hour project (dozens of files, ~2 GB WAV) without UI stalls: waveform from pre-computed multi-resolution peaks; playback from proxies where needed.
- Analysis runs as background jobs; UI always responsive.
- Transcription/alignment cached by file hash + settings so re-runs are fast.
- Sample-accurate edits in the EDL; no drift across long timelines.
- No audio leaves the machine; no network access required after models are downloaded.

## 9. Milestones (build in order; each ends with a working, demoable state)

| # | Milestone | Acceptance criteria |
|---|---|---|
| M0 | Scaffolding & spikes | Repo layout, CI-style `make check`, fixtures generator, decision log. Spike results documented: Whisper variant choice, alignment lib choice, Tauri vs. fallback. |
| M1 | Project + ingest | Create/open `.hillproj`; import `.mp3`/`.wav`; hash, probe, peaks; sources immutable (test proves bytes unchanged). |
| M2 | Text import & page mapping | `.txt/.docx/.pdf` import; page offset; manuscript units with pages; unit tests on page edge cases. |
| M3 | Transcribe + Compare to Text | Word-level transcripts; alignment with per-unit scores; report on fixtures matches injected errors above target accuracy. |
| M4 | Issues pipeline | Flubs and overlong silences flagged with timestamp + page; Issues panel; jump-to-audio; re-run with diff and preserved decisions. |
| M5 | Cleanup + EDL | Stutter/false-start/commentary removal as EDL ops; Removed Material panel; undo/redo; rolling-edit restore verified by test. |
| M6 | Takes | Take groups detected; comparison UI; preferred take updates EDL. |
| M7 | Timeline editor | Multitrack, trim/split/move/fade/gain, snapping, markers; realtime preview from EDL. |
| M8 | Finalize + export | Noise reduction, auto-level, loudness presets with pass/fail report; WAV/MP3 export; narrator guide (PDF/CSV/MD). |
| M9 | macOS packaging | Signed-ready `.app` wrapper; first-run model download; web mode still works. |
| M10 | Hardening | Long-project performance pass, crash recovery, accessibility, docs. |

## 10. Quality gates (every milestone)
- Unit tests for logic; golden tests for alignment/diff; integration tests through the API.
- A **round-trip test**: import → auto-edit → undo all → render equals the original (sample-identical).
- Non-destructive test: source file hashes identical before/after any workflow.
- Lint/type-check clean (`ruff`, `mypy`, `tsc --noEmit`, ESLint).
- No milestone is "done" until its acceptance criteria are demonstrated and recorded in `docs/PROGRESS.md`.

## 11. Test fixtures (important)
Real narration is not needed to develop this. Create a **fixture generator** (`scripts/make_fixtures.py`) that:
- Takes a short public-domain text.
- Synthesizes narration with macOS `say` (or another local TTS) into `.wav`.
- Programmatically injects known defects: repeated words (stutters), restarted phrases, inserted commentary ("sorry, let me redo that"), skipped/misread words, long silences, duplicate takes of select paragraphs, added background noise at known dB levels.
- Emits a JSON "ground truth" file listing every injected defect with timestamps, so detection precision/recall can be measured automatically.

Keep fixtures small and committed; keep any large audio out of git (`.gitignore`, document how to regenerate).

## 12. Privacy and licensing
- Local-only processing; document model download sources and sizes.
- Check licenses of every dependency and model before bundling; record in `docs/LICENSES.md`.
- Do not commit user manuscripts or recordings.

## 13. Open questions (decide early; log answers in `docs/DECISIONS.md`)
1. Typical project size (hours of audio, number of takes)? Determines performance work.
2. Is the primary export target ACX/Audible, or general-purpose?
3. Manuscript formats most often used (`.docx`, PDF, plain text)? Drives page-mapping priority.
4. Should timestamps in the narrator guide reference the original recordings, the edited master, or both?
5. Minimum macOS version and Apple Silicon only, or Intel too?
6. Is a MIDI/music-bed track ever needed (affects timeline scope)?

## 14. References
- ACX audio submission requirements (RMS, peak, noise floor): https://www.acx.com/help/rules-for-audiobook-production/200485520 — confirm current values on acx.com.
- faster-whisper: https://github.com/SYSTRAN/faster-whisper
- mlx-whisper (Apple Silicon): https://github.com/ml-explore/mlx-examples/tree/main/whisper
- WhisperX (forced alignment): https://github.com/m-bain/whisperX
- stable-ts: https://github.com/jianfch/stable-ts
- pedalboard: https://github.com/spotify/pedalboard
- pyloudnorm: https://github.com/csteinmetz1/pyloudnorm
- noisereduce: https://github.com/timsainb/noisereduce
- Tauri (macOS wrapper): https://tauri.app
