# HillAudiobook

A first-pass audiobook editing app. It transcribes narration, compares it to the
manuscript, removes stutters and off-script commentary non-destructively, flags
flubs and overlong silences with timestamps and page numbers, compares takes, and
exports audio plus a narrator corrections guide. Everything runs locally.

- What to build: [`SPEC.md`](SPEC.md)
- How to work on it: [`CLAUDE.md`](CLAUDE.md)
- Status: [`docs/PROGRESS.md`](docs/PROGRESS.md) · decisions: [`docs/DECISIONS.md`](docs/DECISIONS.md)

## Requirements
- Python 3.11+, Node 20+, ffmpeg
- espeak-ng (only to regenerate test fixtures): `brew install espeak-ng`

## Commands
```
make setup      # venv, deps, npm install, check ffmpeg
make dev        # engine (127.0.0.1:8765) + web (http://127.0.0.1:5173)
make test       # engine tests + web tests
make check      # ruff, mypy, tsc --noEmit, eslint, tests
make fixtures   # regenerate synthetic test audio + ground truth
make e2e        # browser end-to-end tests (Playwright; set CHROMIUM_PATH to reuse a local Chromium)
```

## Test fixtures
`engine/fixtures/` holds about 66 s of synthetic narration (2.1 MB) with known,
injected defects, and `ground_truth.json` lists every defect with exact
timestamps. Regenerate with `make fixtures`; with the same espeak-ng version the
output is byte-identical. No real recordings, manuscripts or model weights are
ever committed.
