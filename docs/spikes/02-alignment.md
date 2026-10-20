# Spike 02: Alignment library (stable-ts vs WhisperX)

- **Date:** 2026-10-20 · **Time box:** 1 hour · **Status:** decided provisionally; neither is the default
- **Question:** how do we get word boundaries tight enough to cut on, and align heard speech to the manuscript?

## Two different problems

SPEC §3 lists "alignment" as one layer, but the product needs two things:
1. **Manuscript ↔ transcript alignment** (Compare to Text, flubs, takes). The text is
   known, but the narrator departs from it. This is a sequence-alignment problem
   over words, and it must *report* departures, not force them to fit.
2. **Precise word boundaries** for cut points (stutter/commentary removal, take
   swaps). This is an acoustic problem.

Forced aligners solve (2) by assuming the text is what was said. That is exactly
wrong for (1): forcing the manuscript onto a misread line hides the misread. So
(1) is our own code (rapidfuzz plus word-level sequence alignment, as SPEC §3
already says), and a library is only a candidate for (2).

## Findings

**stable-ts** [1][2]
- MIT, 2.19.1 (2025-08-16). Aligns plain text to audio at word level
  (`model.align(audio, text, language=...)`), and has `refine()` to tighten
  timestamps. The docs say `refine()` is "significantly slower" with faster-whisper models.
- Requires `torch`, `torchaudio` and `openai-whisper` unconditionally; faster-whisper
  and mlx are optional extras.
- **The README states that development is "indefinitely paused", and the repository
  was archived on 2026-05-30 (read-only).** Taking a new dependency on an archived
  project is a maintenance risk for a product we intend to keep shipping.
- Its failure handling is a zero-duration-word threshold (`failure_threshold`). It
  has no notion of reporting which words the speaker got wrong.

**WhisperX** [3][4]
- BSD-2-Clause, 3.8.6 (2026-05-25). Word timestamps come from wav2vec2 phoneme
  alignment; built-in English models via torchaudio.
- Pins `torch~=2.8.0`, `torchaudio~=2.8.0`, `torchvision~=0.23.0`, requires
  `pyannote-audio>=4.0.0` (diarization, which we don't need), and is limited to
  Python `<3.14`.
- Documented limitations: transcript words with characters outside the aligner's
  dictionary (the README's examples include numbers and currency) get no timing.
  Audiobooks contain numbers, so those words would need fallback timing anyway.

## Decision (provisional, D-008)

- **Default:** faster-whisper word timestamps, plus our own boundary refinement.
  This is a pure function in `analysis/` that snaps each cut point to the
  lowest-energy instant within a small window (initially ±80 ms) around the ASR
  boundary. Cuts belong in pauses, and pauses are easy to find acoustically. This
  needs no torch, works for numerals, and is easy to test against the fixtures'
  exact ground truth.
- **Manuscript comparison:** own word-level alignment (rapidfuzz, MIT, 3.14.6 [5])
  in `analysis/`, as SPEC §3 specifies.
- **Re-evaluate WhisperX** in M3, as an optional refinement backend, only if boundary
  error on fixtures and real narration misses the target: median ≤ 50 ms and
  p95 ≤ 150 ms after snapping, measured at sentence edges. These targets are my
  choice and are recorded in DECISIONS.md.
- **stable-ts:** not adopted, because it is archived.

## Sources
1. stable-ts repository and README: https://github.com/jianfch/stable-ts
2. PyPI, stable-ts: https://pypi.org/project/stable-ts/
3. WhisperX repository and README: https://github.com/m-bain/whisperX
4. PyPI, whisperx: https://pypi.org/project/whisperx/
5. PyPI, rapidfuzz: https://pypi.org/project/rapidfuzz/
