# HillAudiobook — Working Rules for Claude Code

First-pass audiobook editing app (macOS native + web). Full requirements are in `SPEC.md`. Read it before planning any work.

## How to work
1. **Plan before coding.** For each milestone in `SPEC.md` §9, write a short plan (files to touch, tests to add, risks) and proceed. If a decision isn't covered by the spec, make the simplest reasonable choice, and record it in `docs/DECISIONS.md` with the reason.
2. **Build milestone by milestone, in order.** Don't start a milestone until the previous one's acceptance criteria pass. Keep a running checklist in `docs/PROGRESS.md`.
3. **Small, verified steps.** Run tests after every meaningful change. Commit at each green state with a clear message.
4. **Ask only when blocked** on something that is the user's call (see SPEC §13). Otherwise decide, document, and move on.
5. **Spike unknowns first** (Whisper variant, alignment library, Tauri). Time-box them and write down results.

## Non-negotiable rules
- **Never modify, move, or delete imported source audio or manuscripts.** Edits live only in the EDL / project DB. Add and keep a test that proves source file hashes are unchanged.
- **Every automated change must be logged, labeled `auto`, and reversible.** No hidden destructive operations.
- **Re-running checks must preserve user decisions** (stable issue keys).
- **Never transcode lossy → lossy.** Render exports from original sources.
- **Local only.** No network calls with user audio or text, ever.
- Do not commit user audio, manuscripts, or model weights. Fixtures must be generated or tiny.

## Stack and commands
Python 3.11+ engine (FastAPI), React + TypeScript + Vite frontend, SQLite, ffmpeg. Tauri wrapper comes late (M9).

Maintain these entry points (create them in M0 and keep them working):
```
make setup      # venv, deps, npm install, check ffmpeg
make dev        # engine + web dev servers
make test       # engine tests + web tests
make check      # ruff, mypy, tsc --noEmit, eslint, tests
make fixtures   # regenerate synthetic test audio + ground truth
```
If a command in this file is wrong or missing, fix the Makefile or this file immediately.

## Conventions
- Engine: type hints everywhere, `ruff` + `mypy` clean, pure functions in `analysis/` (no I/O) so they're easy to test.
- All timestamps in the engine are **float seconds from source start**; convert to sample indices only at the audio boundary. Use sample-accurate integers in the EDL where drift matters.
- Long work runs as cancellable background jobs with progress reported over WebSocket.
- Cache expensive results (transcripts, alignments, peaks) keyed by file hash + settings.
- UI: keyboard shortcuts for every review action; no feature that requires a mouse only.
- Prefer small modules and clear names over cleverness. Comment the *why*.

## Testing expectations
- Golden/ground-truth tests using generated fixtures (SPEC §11); report precision/recall for flub, silence, stutter, and take detection.
- Round-trip test: auto-edit then undo-all renders identical to the original.
- Every bug fix gets a regression test.

## When finishing a milestone
1. All acceptance criteria demonstrated by tests or a scripted demo.
2. `make check` passes.
3. Update `docs/PROGRESS.md` and `docs/DECISIONS.md`.
4. Summarize what changed, what's deferred, and any risks in a short note.
