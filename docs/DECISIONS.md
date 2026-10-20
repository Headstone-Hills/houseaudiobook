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
