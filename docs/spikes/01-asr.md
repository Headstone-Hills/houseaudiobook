# Spike 01: Transcription backend (faster-whisper vs mlx-whisper)

- **Date:** 2026-10-20 · **Time box:** 1 hour · **Status:** decided; benchmarked in M3 (see Results below)
- **Question:** which local Whisper variant should be the default for word-level transcripts?

## What could and could not be tested here

The development container is Linux x86-64 with no GPU. Its network policy blocks
`huggingface.co` and OpenAI's model CDN, so **no model weights could be downloaded**.
Neither backend could transcribe a single fixture, and mlx-whisper cannot run on
Linux anyway (it needs Apple's MLX framework). Everything below is installation
facts measured here, plus primary documentation. Accuracy and speed numbers must
come from the M3 benchmark on real hardware.

## Findings

| | faster-whisper | mlx-whisper |
|---|---|---|
| Latest version | 1.2.1 (2025-10-31) [1] | 0.4.3 (2025-08-29) [2] |
| License | MIT [1] | MIT [2] |
| Runtime | CTranslate2 (CPU, CUDA; Apple Silicon CPU) | MLX (Apple Silicon GPU) |
| Platforms | macOS, Linux, Windows | Apple Silicon only |
| Word timestamps | `word_timestamps=True` on `transcribe` (verified in the installed 1.2.1 signature) | `word_timestamps=True`, words in each segment [3] |
| Built-in VAD | `vad_filter` (Silero via onnxruntime) | none |
| Torch dependency | no [1] | yes, `torch` is listed in `requires_dist` [2] |
| Install size here | 455 MB site-packages (ctranslate2 137 MB, av 104 MB, onnxruntime 68 MB, numpy 45 MB) | not installable here |
| Model names | `tiny` … `large-v3`, `large-v3-turbo`, `distil-*` (from `faster_whisper.utils._MODELS`) | `mlx-community/*` checkpoints on HF [3] |

### A risk that matters more than the backend: Whisper "cleans up" speech

Whisper was trained to write fluent transcripts, and this product needs the
disfluencies:
- CrisperWhisper's authors argue that Whisper's default output style does not support
  the detection, categorization, or analysis of disfluencies, and fine-tune it to
  transcribe verbatim [4].
- A 2025 Interspeech study found that Whisper performs worse on sound repetitions,
  and that those repetitions trigger hallucinations more than 20% of the time [5].
- A field evaluation found that Whisper drops "um"/"uh" but keeps most other
  disfluencies [6].

So stutters and false starts may simply be **missing from the transcript**, not
mislabeled. CrisperWhisper would address this, but its weights are under a
non-commercial research license, and commercial use needs a separate license [7].

I could not open the arXiv paper [4] itself: `arxiv.org` doesn't resolve from this
container. The claim is cited from the search-result summary of that paper; verify
it against the paper.

## Decision (provisional, D-007 and D-008 in DECISIONS.md)

1. **faster-whisper is the default backend**, behind a small `asr` interface. It is
   the only option that runs everywhere the web mode might run, it has word
   timestamps and VAD built in, and it doesn't pull in torch.
2. **mlx-whisper becomes an optional backend** on Apple Silicon, but only if the M3
   benchmark shows a real speed win at equal word accuracy. Both use the same
   Whisper weights family, so accuracy should be similar; speed is the open question.
3. **Detection must not trust the transcript alone.** Cleanup (M5) will also flag
   speech the transcript doesn't cover, by comparing VAD/energy speech regions with
   transcript word spans. Low-confidence findings go to the action list as
   *suggested*, never auto-cut (SPEC §6.3 step 4).

## M3 benchmark plan (exit criteria for this decision)

Run each candidate (`small.en`, `medium.en`, `large-v3-turbo`; on a Mac add
mlx-whisper) against `engine/fixtures/`, and against at least one real human
recording the user supplies locally (never committed). Report:
- word error rate against the *heard* text in `ground_truth.json`;
- the share of injected stutters and false starts that appear in the transcript at all;
- median and p95 word-boundary error against ground-truth sentence edges;
- real-time factor on the target Mac.

Caveat: espeak-ng fixtures are robotic, so they will underestimate the "cleanup"
problem. Real narration is needed before this decision is final.

## Sources
1. PyPI, faster-whisper: https://pypi.org/project/faster-whisper/ · repo: https://github.com/SYSTRAN/faster-whisper
2. PyPI, mlx-whisper: https://pypi.org/project/mlx-whisper/
3. mlx-examples Whisper README: https://github.com/ml-explore/mlx-examples/tree/main/whisper
4. "CrisperWhisper: Accurate Timestamps on Verbatim Speech Transcriptions" (2024): https://arxiv.org/abs/2408.16589
5. Sridhar et al., Interspeech 2025: https://www.isca-archive.org/interspeech_2025/sridhar25_interspeech.pdf
6. Cortico, "Evaluating OpenAI's Whisper on Community Conversations": https://cortico.ai/news/insights/evaluating-openai-s-whisper-on-community-conversations/
7. CrisperWhisper repository (license section): https://github.com/nyrahealth/CrisperWhisper

## Results (M3, 2026-10-20)

Once huggingface.co and *.hf.co were allowed, `scripts/bench_asr.py` ran
tiny.en, small.en, medium.en and large-v3-turbo on the fixtures. The full table is in
`docs/bench/asr-2026-10-20.md`.

- **The "cleanup" risk is real, and a prompt mitigates it.** No model kept the injected
  stutters unprompted. With `initial_prompt="Um, uh, the- the, I mean, sorry, let me
  start again."` small, medium and turbo kept all of them. False starts, commentary,
  misreads and skips survived in every configuration.
- **small.en + prompt** had 0.0% verbatim WER at 0.19x real time. That is the best on
  this CPU: larger models were no more accurate and cost 1.5-2.5x more.
- **Timestamps** need snapping (D-008): raw p95 error was 500 ms; snapped, 10-12 ms.

**Decision (D-028):** small.en with the disfluency prompt is the default; medium.en
is an option for users who want it. This is to be re-checked on a real recording,
because espeak-ng is not a human voice.
