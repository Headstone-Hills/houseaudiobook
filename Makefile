# HillAudiobook entry points (see CLAUDE.md). Keep these working.

PYTHON ?= python3.11
VENV   := engine/.venv
PY     := $(VENV)/bin/python
NPM    ?= npm

.PHONY: setup dev serve web-build test test-engine test-web check lint typecheck fixtures e2e clean

setup: $(VENV)/.installed web/node_modules/.installed
	@command -v ffmpeg >/dev/null || { echo "error: ffmpeg not found (brew install ffmpeg / apt install ffmpeg)"; exit 1; }
	@command -v ffprobe >/dev/null || { echo "error: ffprobe not found (ships with ffmpeg)"; exit 1; }
	@command -v espeak-ng >/dev/null || echo "note: espeak-ng not found; needed only for 'make fixtures' (brew install espeak-ng / apt install espeak-ng)"
	@echo "setup ok"

$(VENV)/.installed: engine/pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -e "engine[dev,asr]"
	@touch $@

web/node_modules/.installed: web/package.json web/package-lock.json
	cd web && $(NPM) ci --no-audit --no-fund
	@touch $@

# Engine on :8765, Vite on :5173 (proxies /api to the engine). Ctrl-C stops both.
dev: setup
	@trap 'kill 0' INT TERM EXIT; \
	(cd engine && .venv/bin/python -m hillaudio.api.app) & \
	(cd web && $(NPM) run dev) & \
	wait

# Production web mode: the built UI and the API on one port (what the app runs).
serve: setup web-build
	$(PY) -m hillaudio serve --port $${PORT:-8765} --web-dir web/dist

web-build: web/node_modules/.installed
	cd web && $(NPM) run build --silent

test: test-engine test-web

test-engine: $(VENV)/.installed
	cd engine && .venv/bin/python -m pytest -q

test-web: web/node_modules/.installed
	cd web && $(NPM) test --silent

lint: $(VENV)/.installed web/node_modules/.installed
	cd engine && .venv/bin/ruff check . ../scripts && .venv/bin/ruff format --check . ../scripts
	cd web && $(NPM) run lint --silent

typecheck: $(VENV)/.installed web/node_modules/.installed
	cd engine && .venv/bin/mypy
	cd web && $(NPM) run typecheck --silent

check: lint typecheck test
	@echo "check ok"

# Browser end-to-end tests against the real engine (not part of `check`: slower,
# needs Chromium). Set CHROMIUM_PATH to use a preinstalled browser.
e2e: setup
	cd web && $(NPM) run e2e

fixtures: $(VENV)/.installed
	$(PY) scripts/make_fixtures.py

clean:
	rm -rf $(VENV) web/node_modules web/dist
